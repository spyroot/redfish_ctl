"""Dual-mode tests for the guarded firmware-update command."""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

SIMPLE_UPDATE_TARGET = (
    "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"
)


def _post_requests(redfish_service):
    """Return POST requests recorded by the offline mock service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def test_firmware_update_defaults_to_dry_run_in_dual_mode(redfish_api):
    """firmware-update previews SimpleUpdate by default without POSTing."""
    result = redfish_api.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_uri="https://example.invalid/firmware.exe",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#UpdateService.SimpleUpdate"
    assert result.data["target"] == SIMPLE_UPDATE_TARGET
    assert result.data["payload"] == {
        "ImageURI": "https://example.invalid/firmware.exe"
    }
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"


def test_firmware_update_dry_run_keeps_confirm_from_posting(redfish_api):
    """firmware-update --dry_run remains a preview even with --confirm."""
    result = redfish_api.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_uri="https://example.invalid/firmware.exe",
        transfer_protocol="HTTPS",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["target"] == SIMPLE_UPDATE_TARGET
    assert result.data["payload"] == {
        "ImageURI": "https://example.invalid/firmware.exe",
        "TransferProtocol": "HTTPS",
    }
    assert result.data["blocked"] is None


def test_firmware_update_confirm_posts_payload_in_mock_mode(
    redfish_mock, redfish_service
):
    """firmware-update --confirm POSTs the image payload to SimpleUpdate."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_uri="https://example.invalid/firmware.exe",
        transfer_protocol="HTTPS",
        confirm=True,
    )

    posts = [
        request
        for request in _post_requests(redfish_service)
        if request.path.lower() == SIMPLE_UPDATE_TARGET.lower()
    ]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#UpdateService.SimpleUpdate"
    assert result.data["target"] == SIMPLE_UPDATE_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].json() == {
        "ImageURI": "https://example.invalid/firmware.exe",
        "TransferProtocol": "HTTPS",
    }
