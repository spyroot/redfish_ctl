"""Offline tests for the vendor-neutral action foundation + its safety guard.

Covers the destructiveness policy, the invoke_action primitive's discover-then-
gate behavior against the GB300 corpus, and — critically — the negative contract:
a DESTRUCTIVE/IRREVERSIBLE action must NOT POST without explicit confirm. All run
against tests/supermicro_fixtures/ through the real requests path; the mock
records every POST so we can assert exactly what did (or did not) fire.
"""
from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import RedfishApiRespond


def _post_count(svc):
    """Number of POSTs the mock recorded (action fires are POSTs)."""
    return sum(1 for r in svc.requests if r.method == "POST")


def test_policy_classifies_known_and_unknown():
    """Known actions map to their level; an unmapped action fails safe."""
    assert classify("#ComputerSystem.Reset") is Destructiveness.DESTRUCTIVE
    assert classify("#Drive.SecureErase") is Destructiveness.IRREVERSIBLE
    assert classify("#EventService.SubmitTestEvent") is Destructiveness.REVERSIBLE
    reversible_actions = (
        "#DellLCService.TestNetworkShare",
    )
    assert classify("#HpeServerChassis.DisableMCTPOnServer") is Destructiveness.DESTRUCTIVE
    assert classify("#HpeServerChassis.FactoryResetMCTP") is Destructiveness.IRREVERSIBLE
    assert classify("#DelliDRACCardService.TestSEKMServerConnection") is (
        Destructiveness.REVERSIBLE
    )
    hpe_reversible_actions = (
        "#HpeDirectoryTest.StartTest",
        "#HpeDirectoryTest.StopTest",
        "#HpeiLOSnmpService.SendSNMPTestAlert",
        "#HpeiLOManagerNetworkService.SendTestAlertMail",
        "#HpeiLOManagerNetworkService.SendTestSyslog",
    )
    for action in reversible_actions:
        assert classify(action) is Destructiveness.REVERSIBLE
    for action in hpe_reversible_actions:
        assert classify(action) is Destructiveness.REVERSIBLE
    dell_reversible_actions = (
        "#DelliDRACCardService.SendTestEmailAlert",
        "#DelliDRACCardService.SendTestSNMPTrap",
        "#DelliDRACCardService.TestRsyslogServerConnection",
    )
    for action in dell_reversible_actions:
        assert classify(action) is Destructiveness.REVERSIBLE
    assert classify("#ComponentIntegrity.SPDMGetSignedMeasurements") is Destructiveness.READ_ONLY
    assert classify("#LicenseService.Install") is Destructiveness.DESTRUCTIVE
    assert classify("#HpeiLOAccountService.ImportKerberosKeytab") is Destructiveness.DESTRUCTIVE
    assert classify("#DellLCService.ExportLCLog") is Destructiveness.DESTRUCTIVE
    assert classify("#DellBIOSService.DeviceRecovery") is Destructiveness.DESTRUCTIVE
    assert classify("#DellLCService.SupportAssistExportLastCollection") is (
        Destructiveness.DESTRUCTIVE
    )
    assert classify("#TelemetryService.ClearMetricReports") is Destructiveness.DESTRUCTIVE
    assert classify("#SmcNodeManager.ClearAllPolicies") is Destructiveness.DESTRUCTIVE
    assert (
        classify("#TelemetryService.ResetMetricReportDefinitionsToDefaults")
        is Destructiveness.DESTRUCTIVE
    )
    assert classify("#DellJobService.SetupJobQueue") is Destructiveness.DESTRUCTIVE
    assert classify("#DellLCService.ExposeiSMInstallerToHostOS") is (
        Destructiveness.DESTRUCTIVE
    )
    card_group_actions = (
        "#DelliDRACCardService.DeleteGroup",
        "#DelliDRACCardService.JoinGroup",
        "#DelliDRACCardService.RemoveSelf",
    )
    for action in card_group_actions:
        assert classify(action) is Destructiveness.DESTRUCTIVE
    assert classify("#SecureBoot.ResetKeys") is Destructiveness.DESTRUCTIVE
    assert classify("#SecureBootDatabase.ResetKeys") is Destructiveness.DESTRUCTIVE
    # unmapped / empty -> DESTRUCTIVE (cannot fire without --confirm)
    assert classify("#Some.BrandNewAction") is Destructiveness.DESTRUCTIVE
    assert classify(None) is Destructiveness.DESTRUCTIVE


def test_request_log_payload_redacts_sensitive_fields():
    """Debug request logging masks secrets without mutating the real payload."""
    payload = {
        "UserName": "root",
        "Password": "secret-value",
        "Nested": {
            "OldPassword": "old-secret",
            "NewPassword": "new-secret",
            "ClientSecret": "client-secret",
            "RefreshToken": "refresh-token",
            "PasswordName": "Administrator",
        },
        "Links": [
            {"token": "bearer-value"},
            {"ApiKey": "api-key-value"},
            {"AccessKey": "access-key-value"},
            {"Label": "public"},
        ],
    }

    redacted = IDracManager._redact_sensitive_payload(payload)

    assert redacted["Password"] == "********"
    assert redacted["Nested"]["OldPassword"] == "********"
    assert redacted["Nested"]["NewPassword"] == "********"
    assert redacted["Nested"]["ClientSecret"] == "********"
    assert redacted["Nested"]["RefreshToken"] == "********"
    assert redacted["Nested"]["PasswordName"] == "Administrator"
    assert redacted["Links"][0]["token"] == "********"
    assert redacted["Links"][1]["ApiKey"] == "********"
    assert redacted["Links"][2]["AccessKey"] == "********"
    assert redacted["Links"][3]["Label"] == "public"
    assert payload["Password"] == "secret-value"
    assert payload["Nested"]["OldPassword"] == "old-secret"


def test_invoke_resolves_target_from_actions_block(redfish_mock_factory):
    """invoke_action discovers the real GB300 target, not a hardcoded path."""
    mgr, svc = redfish_mock_factory("supermicro")
    # reversible action executes and POSTs to the discovered EventService target
    result = mgr.invoke_action("/redfish/v1/EventService", "SubmitTestEvent",
                               payload={"MessageId": "Alert.1.0.TestEvent"},
                               full_action_type="#EventService.SubmitTestEvent")
    assert result.data.get("executed") is True
    assert result.data["target"] == "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    assert _post_count(svc) == 1


def test_invoke_action_rejected_post_reports_error(redfish_mock_factory, monkeypatch):
    """A rejected action POST must not be reported as successful execution."""
    mgr, svc = redfish_mock_factory("supermicro")
    calls = []

    def rejected_post(resource, payload=None, do_async=False, expected_status=202):
        calls.append((resource, payload, do_async, expected_status))
        return (
            CommandResult({"Status": "error"}, None, None, "429 Too Many Requests"),
            RedfishApiRespond.Error,
        )

    monkeypatch.setattr(mgr, "base_post", rejected_post)

    result = mgr.invoke_action("/redfish/v1/EventService", "SubmitTestEvent",
                               payload={"MessageId": "Alert.1.0.TestEvent"},
                               full_action_type="#EventService.SubmitTestEvent")

    assert calls == [(
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent",
        {"MessageId": "Alert.1.0.TestEvent"},
        False,
        202,
    )]
    assert result.error == "429 Too Many Requests"
    assert result.data["Status"] == "error"
    assert result.data["executed"] is False
    assert result.data["action"] == "#EventService.SubmitTestEvent"
    assert result.data["target"] == "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    assert _post_count(svc) == 0


def test_invoke_action_rejected_post_without_error_uses_status_fallback(
    redfish_mock_factory, monkeypatch
):
    """A rejected action POST still returns an actionable error without details."""
    mgr, svc = redfish_mock_factory("supermicro")

    def rejected_post(resource, payload=None, do_async=False, expected_status=202):
        return CommandResult({"Status": "error"}, None, None, None), RedfishApiRespond.Error

    monkeypatch.setattr(mgr, "base_post", rejected_post)

    result = mgr.invoke_action(
        "/redfish/v1/EventService",
        "SubmitTestEvent",
        payload={"MessageId": "Alert.1.0.TestEvent"},
        full_action_type="#EventService.SubmitTestEvent",
    )

    assert result.error == "action #EventService.SubmitTestEvent failed with Error"
    assert result.data["executed"] is False
    assert _post_count(svc) == 0


class _WriteErrorResponse:
    status_code = 405
    headers = {}

    def json(self):
        return {
            "error": {
                "code": "Base.1.18.GeneralError",
                "message": "write rejected",
            }
        }


def test_base_post_error_response_carries_parsed_error(
    redfish_mock_factory, monkeypatch
):
    """A rejected write returns its parsed Redfish error with the result."""
    mgr, _svc = redfish_mock_factory("supermicro")
    monkeypatch.setattr(
        mgr,
        "api_post_call",
        lambda *_args, **_kwargs: _WriteErrorResponse(),
    )

    result, api_resp = mgr.base_post(
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent",
        payload={"MessageId": "Alert.1.0.TestEvent"},
        expected_status=202,
    )

    assert api_resp == RedfishApiRespond.Error
    assert result.error is mgr._redfish_error
    assert result.error.status_code == 405
    assert result.error.code == "Base.1.18.GeneralError"


def test_destructive_blocks_without_confirm(redfish_mock_factory):
    """A DESTRUCTIVE action defaults to a dry-run and POSTs nothing."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.invoke_action("/redfish/v1/Systems/System_0", "Reset",
                               payload={"ResetType": "GracefulRestart"},
                               full_action_type="#ComputerSystem.Reset")
    assert result.data["dry_run"] is True
    assert result.data["level"] == "destructive"
    assert result.data["blocked"]  # explains it needs --confirm
    assert result.data["target"] == "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset"
    assert _post_count(svc) == 0, "destructive action must not POST without confirm"


def test_destructive_fires_with_confirm(redfish_mock_factory):
    """The same action POSTs once --confirm is given."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.invoke_action("/redfish/v1/Systems/System_0", "Reset",
                               payload={"ResetType": "GracefulRestart"},
                               full_action_type="#ComputerSystem.Reset",
                               confirm=True)
    assert result.data.get("executed") is True
    assert _post_count(svc) == 1
    last = svc.last_request
    assert last.method == "POST"
    assert last.json() == {"ResetType": "GracefulRestart"}


def test_irreversible_needs_both_tokens(redfish_mock_factory):
    """An IRREVERSIBLE action stays a dry-run with only --confirm.

    #Manager.ResetToDefaults (factory-reset the BMC) needs --confirm AND the
    explicit irreversible token; --confirm alone must not fire it.
    """
    mgr, svc = redfish_mock_factory("supermicro")
    confirm_only = mgr.invoke_action("/redfish/v1/Managers/BMC_0", "ResetToDefaults",
                                     payload={"ResetType": "ResetAll"},
                                     full_action_type="#Manager.ResetToDefaults",
                                     confirm=True)
    assert confirm_only.data["dry_run"] is True
    assert _post_count(svc) == 0, "irreversible must not fire with --confirm alone"

    both = mgr.invoke_action("/redfish/v1/Managers/BMC_0", "ResetToDefaults",
                             payload={"ResetType": "ResetAll"},
                             full_action_type="#Manager.ResetToDefaults",
                             confirm=True, confirm_irreversible=True)
    assert both.data.get("executed") is True
    assert _post_count(svc) == 1


def test_unknown_action_reports_available(redfish_mock_factory):
    """Asking for a non-existent action returns an error + the available set."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.invoke_action("/redfish/v1/Systems/System_0", "NoSuchAction")
    assert result.error and "not found" in result.error
    assert _post_count(svc) == 0
