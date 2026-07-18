"""Dual-mode coverage for Dell OS deployment VFlash actions."""

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.delloem.cmd_dell_os_vflash_actions import DellOsVflashActions
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

OS_DEPLOYMENT = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellOSDeploymentService"
ACTION_PREFIX = f"{OS_DEPLOYMENT}/Actions/DellOSDeploymentService"


def _post_requests(service):
    """Return POST requests recorded by the offline mock service."""
    return [request for request in service.requests if request.method == "POST"]


def _service_body(service):
    """Return a mutable DellOSDeploymentService body from fixture-backed state."""
    return dict(service._state(OS_DEPLOYMENT))


def _set_os_deployment(service, body):
    """Overlay DellOSDeploymentService under both path casings used by requests-mock."""
    service._overlay[OS_DEPLOYMENT] = body
    service._overlay[OS_DEPLOYMENT.lower()] = body


def test_dell_os_vflash_actions_lists_corpus_backed_targets(redfish_api):
    """The command lists VFlash-related actions from DellOSDeploymentService."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellOsVflashActions,
        "dell-os-vflash-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    rows = result.data["os_deployment_targets"]
    assert len(rows) == 1
    assert rows[0]["System"] == "System.Embedded.1"
    assert rows[0]["Uri"] == OS_DEPLOYMENT
    actions = {action["Action"]: action for action in rows[0]["Actions"]}
    assert set(actions) == {
        "boot-hd",
        "boot-vflash-iso",
        "delete-vflash-iso",
        "detach-drivers",
        "detach-vflash-iso",
        "skip-iso-boot",
    }
    assert actions["detach-vflash-iso"]["Target"] == (
        f"{ACTION_PREFIX}.DetachISOFromVFlash"
    )


def test_dell_os_vflash_action_defaults_to_dry_run(redfish_api, redfish_service):
    """A selected action previews by default and does not POST."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellOsVflashActions,
        "dell-os-vflash-actions",
        action="detach-vflash-iso",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellOSDeploymentService.DetachISOFromVFlash",
        "target": f"{ACTION_PREFIX}.DetachISOFromVFlash",
        "payload": {},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(redfish_service) == []


def test_dell_os_vflash_action_confirm_posts_empty_payload(
        redfish_api, redfish_service):
    """With --confirm the command POSTs to the discovered action target."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellOsVflashActions,
        "dell-os-vflash-actions",
        action="skip-iso-boot",
        confirm=True,
    )

    posts = [
        request
        for request in _post_requests(redfish_service)
        if request.path.lower() == f"{ACTION_PREFIX}.SkipISOImageBoot".lower()
    ]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellOSDeploymentService.SkipISOImageBoot"
    assert result.data["target"] == f"{ACTION_PREFIX}.SkipISOImageBoot"
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].json() == {}


def test_dell_os_vflash_action_dry_run_overrides_confirm(
        redfish_api, redfish_service):
    """--dry_run keeps the command in preview mode even with --confirm."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellOsVflashActions,
        "dell-os-vflash-actions",
        action="delete-vflash-iso",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["action"] == "#DellOSDeploymentService.DeleteISOFromVFlash"
    assert _post_requests(redfish_service) == []


def test_dell_os_vflash_action_reports_missing_action(
        redfish_api, redfish_service):
    """A service missing the selected action returns an actionable error."""
    body = _service_body(redfish_service)
    body["Actions"] = dict(body["Actions"])
    del body["Actions"]["#DellOSDeploymentService.BootToHD"]
    _set_os_deployment(redfish_service, body)

    result = redfish_api.sync_invoke(
        ApiRequestType.DellOsVflashActions,
        "dell-os-vflash-actions",
        action="boot-hd",
        confirm=True,
    )

    assert result.error == (
        "action '#DellOSDeploymentService.BootToHD' not found on "
        f"{OS_DEPLOYMENT}"
    )
    assert "#DellOSDeploymentService.SkipISOImageBoot" in result.data["available"]
    assert _post_requests(redfish_service) == []


def test_dell_os_vflash_actions_are_registered_and_guarded():
    """The command is registered and its selected actions are destructive."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellOsVflashActions][
        "dell-os-vflash-actions"
    ] is DellOsVflashActions

    for action in (
        "#DellOSDeploymentService.BootToHD",
        "#DellOSDeploymentService.BootToISOFromVFlash",
        "#DellOSDeploymentService.DeleteISOFromVFlash",
        "#DellOSDeploymentService.DetachDrivers",
        "#DellOSDeploymentService.DetachISOFromVFlash",
        "#DellOSDeploymentService.SkipISOImageBoot",
    ):
        assert classify(action) is Destructiveness.DESTRUCTIVE

    cmd_parser, cmd_name, cmd_help = DellOsVflashActions.register_subcommand(
        DellOsVflashActions
    )
    help_text = cmd_parser.format_help()
    assert cmd_name == "dell-os-vflash-actions"
    assert "VFlash" in cmd_help
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
