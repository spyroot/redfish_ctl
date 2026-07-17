"""Dual-mode tests for selected HPE iLO Manager OEM actions."""
from redfish_ctl.oem.cmd_hpe_manager_actions import HpeManagerActions
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

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
    assert actions["retry-cloud-connect"]["Target"] == _RETRY_TARGET
    assert "reset-to-factory-defaults" not in actions
    assert "clear-nvram" not in actions
    assert "disable-ilo-functionality" not in actions
    assert "request-firmware-recovery" not in actions
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
