"""Offline tests for the vendor-neutral action foundation + its safety guard.

Covers the destructiveness policy, the invoke_action primitive's discover-then-
gate behavior against the GB300 corpus, and — critically — the negative contract:
a DESTRUCTIVE/IRREVERSIBLE action must NOT POST without explicit confirm. All run
against tests/supermicro_fixtures/ through the real requests path; the mock
records every POST so we can assert exactly what did (or did not) fire.
"""
from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.redfish_manager_base import RedfishManagerBase


def _post_count(svc):
    """Number of POSTs the mock recorded (action fires are POSTs)."""
    return sum(1 for r in svc.requests if r.method == "POST")


def test_policy_classifies_known_and_unknown():
    """Known actions map to their level; an unmapped action fails safe."""
    assert classify("#ComputerSystem.Reset") is Destructiveness.DESTRUCTIVE
    assert classify("#Drive.SecureErase") is Destructiveness.IRREVERSIBLE
    assert classify("#EventService.SubmitTestEvent") is Destructiveness.REVERSIBLE
    assert classify("#ComponentIntegrity.SPDMGetSignedMeasurements") is Destructiveness.READ_ONLY
    assert classify("#LicenseService.Install") is Destructiveness.DESTRUCTIVE
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

    redacted = RedfishManagerBase._redact_sensitive_payload(payload)

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
