"""Dual-mode-style tests for the guarded update-start command."""

from redfish_ctl.firmware.cmd_update_start import UpdateStart
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
START_UPDATE_TARGET = (
    "/redfish/v1/UpdateService/Actions/UpdateService.StartUpdate"
)


def _start_update_service():
    """Return an UpdateService body exposing StartUpdate."""
    return {
        "@odata.id": UPDATE_SERVICE_PATH,
        "@odata.type": "#UpdateService.v1_14_0.UpdateService",
        "Id": "UpdateService",
        "Name": "Update Service",
        "Actions": {
            "#UpdateService.StartUpdate": {
                "target": START_UPDATE_TARGET,
            }
        },
    }


def _set_update_service(redfish_service, body):
    """Overlay UpdateService under both path casings used by requests-mock."""
    redfish_service._overlay[UPDATE_SERVICE_PATH] = body
    redfish_service._overlay[UPDATE_SERVICE_PATH.lower()] = body


def _post_requests(redfish_service):
    """Return POST requests recorded by the offline mock service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def test_update_start_defaults_to_dry_run(redfish_mock, redfish_service):
    """update-start previews StartUpdate by default without POSTing."""
    _set_update_service(redfish_service, _start_update_service())

    result = redfish_mock.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#UpdateService.StartUpdate"
    assert result.data["target"] == START_UPDATE_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(redfish_service) == []


def test_update_start_confirm_posts_empty_payload(redfish_mock, redfish_service):
    """update-start --confirm POSTs one StartUpdate request."""
    _set_update_service(redfish_service, _start_update_service())

    result = redfish_mock.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
        confirm=True,
    )

    posts = [
        request
        for request in _post_requests(redfish_service)
        if request.path.lower() == START_UPDATE_TARGET.lower()
    ]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#UpdateService.StartUpdate"
    assert result.data["target"] == START_UPDATE_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].json() == {}


def test_update_start_dry_run_overrides_confirm(redfish_mock, redfish_service):
    """update-start --dry_run remains a preview even with --confirm."""
    _set_update_service(redfish_service, _start_update_service())

    result = redfish_mock.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert _post_requests(redfish_service) == []


def test_update_start_reports_missing_action(redfish_api, redfish_service):
    """An UpdateService without StartUpdate returns an actionable error."""
    result = redfish_api.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#UpdateService.StartUpdate"
    assert "#UpdateService.SimpleUpdate" in result.data["available"]
    assert result.error == (
        "action '#UpdateService.StartUpdate' not found on "
        "/redfish/v1/UpdateService"
    )
    assert _post_requests(redfish_service) == []


def test_update_start_is_registered():
    """The update-start command is wired into the command registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.UpdateStart]["update-start"] is UpdateStart

    cmd_parser, cmd_name, cmd_help = UpdateStart.register_subcommand(UpdateStart)
    help_text = cmd_parser.format_help()

    assert cmd_name == "update-start"
    assert "StartUpdate" in cmd_help or "staged" in cmd_help
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
