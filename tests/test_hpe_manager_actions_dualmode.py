"""Dual-mode tests for selected HPE iLO Manager OEM actions."""
from redfish_ctl.oem.cmd_hpe_manager_actions import HpeManagerActions
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from test_roundtrip_budget import projected_walltime

_MANAGER_URI = "/redfish/v1/Managers/1"
_REST_API_TARGET = (
    "/redfish/v1/Managers/1/Actions/Oem/Hpe/"
    "HpeiLO.ClearRestApiState"
)
_RETRY_TARGET = (
    "/redfish/v1/Managers/1/Actions/Oem/Hpe/"
    "HpeiLO.RetryCloudConnect"
)


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _get_requests(service, path=None):
    """Return GET requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :param path: optional Redfish path to match.
    :return: recorded GET requests.
    """
    return [
        request for request in service.requests
        if request.method == "GET"
        and (path is None or request.path.rstrip("/").lower() == path.lower())
    ]


def test_hpe_manager_actions_list_targets_without_post(redfish_mock_factory):
    """Listing discovers only the supported HPE Manager OEM action subset."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {
        "clear-hotkeys",
        "clear-rest-api-state",
        "disable-cloud-connect",
        "enable-cloud-connect",
        "retry-cloud-connect",
    }
    assert actions["clear-rest-api-state"]["Resource"] == _MANAGER_URI
    assert actions["clear-rest-api-state"]["Target"] == _REST_API_TARGET
    assert "iLO reset" in actions["clear-rest-api-state"]["Note"]
    assert actions["retry-cloud-connect"]["Target"] == _RETRY_TARGET
    assert "cloud services" in actions["enable-cloud-connect"]["Note"]
    assert "cloud services" in actions["retry-cloud-connect"]["Note"]
    assert len(_get_requests(service, _MANAGER_URI)) == 1
    assert "reset-to-factory-defaults" not in actions
    assert "clear-nvram" not in actions
    assert "disable-ilo-functionality" not in actions
    assert "request-firmware-recovery" not in actions
    assert _post_requests(service) == []


def test_hpe_manager_actions_manager_uri_short_circuits_discovery(
    redfish_mock_factory,
):
    """An exact Manager URI reads that manager directly instead of fan-out."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        manager_uri=_MANAGER_URI,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert {row["Action"] for row in result.data} == {
        "clear-hotkeys",
        "clear-rest-api-state",
        "disable-cloud-connect",
        "enable-cloud-connect",
        "retry-cloud-connect",
    }
    assert [request.path for request in _get_requests(service)] == [_MANAGER_URI]
    assert projected_walltime(service, "india-vpn-to-us") <= 0.31
    assert _post_requests(service) == []


def test_hpe_manager_action_dry_runs_by_default(redfish_mock_factory):
    """A selected HPE Manager action resolves the target but does not POST."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        action="clear-rest-api-state",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == "HPE manager action requires --confirm"
    assert result.data["level"] == "destructive"
    assert result.data["payload"] == {}
    assert result.data["target"] == _REST_API_TARGET
    assert "iLO reset" in result.data["note"]
    assert _post_requests(service) == []


def test_hpe_manager_cloud_actions_report_egress_note(redfish_mock_factory):
    """Cloud-connect previews carry the BMC egress consequence."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        action="enable-cloud-connect",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert "cloud services" in result.data["note"]
    assert _post_requests(service) == []


def test_hpe_manager_action_confirm_posts_selected_target(redfish_mock_factory):
    """--confirm posts exactly one selected HPE Manager OEM action."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        action="retry-cloud-connect",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == _RETRY_TARGET.lower()
    assert posts[0].json() == {}


def test_hpe_manager_action_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A non-HPE fixture reports an error and never POSTs."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        action="enable-cloud-connect",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "HPE manager action not found: enable-cloud-connect"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_hpe_manager_action_not_found_keeps_error_without_confirm_label(
    redfish_mock_factory,
):
    """Missing advertised actions are not mislabeled as requiring confirm."""
    manager, service = redfish_mock_factory("hpe")
    manager_body = service._state(_MANAGER_URI.lower())
    manager_body["Oem"]["Hpe"]["Actions"].pop("#HpeiLO.ClearRestApiState")
    service._overlay[_MANAGER_URI] = manager_body
    service._overlay[_MANAGER_URI.lower()] = manager_body

    result = manager.sync_invoke(
        ApiRequestType.HpeManagerActions,
        "hpe-manager-actions",
        action="clear-rest-api-state",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "HPE manager action not found: clear-rest-api-state"
    assert result.data["available"]
    assert "blocked" not in result.data
    assert _post_requests(service) == []


def test_hpe_manager_actions_exposes_cli_entrypoint():
    """The hpe-manager-actions command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.HpeManagerActions]["hpe-manager-actions"] is (
        HpeManagerActions
    )

    cmd_parser, cmd_name, cmd_help = HpeManagerActions.register_subcommand(
        HpeManagerActions
    )

    assert "--action" in cmd_parser.format_help()
    assert cmd_name == "hpe-manager-actions"
    assert "HPE" in cmd_help
