"""Dual-mode tests for the guarded firmware-update command."""

import json
from pathlib import Path

from vendor_corpus import corpus_dir

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

SIMPLE_UPDATE_TARGET = (
    "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"
)
MULTIPART_PUSH_TARGET = "/redfish/v1/UpdateService/update-multipart"
GB300_UPDATE_SERVICE = (
    corpus_dir(Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37")
    / "_redfish_v1_UpdateService.json"
)


def _post_requests(redfish_service):
    """Return POST requests recorded by the offline mock service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def _gb300_update_service():
    """Load the GB300 UpdateService fixture used for push-URI fallback tests."""
    return json.loads(GB300_UPDATE_SERVICE.read_text())


def _set_update_service(redfish_service, body):
    """Overlay UpdateService under both path casings used by requests-mock."""
    redfish_service._overlay["/redfish/v1/UpdateService"] = body
    redfish_service._overlay["/redfish/v1/updateservice"] = body


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


def test_firmware_update_previews_multipart_push_uri_on_gb300(
    redfish_service, redfish_mock
):
    """firmware-update uses GB300 MultipartHttpPushUri when SimpleUpdate is absent."""
    _set_update_service(redfish_service, _gb300_update_service())

    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_file="firmware.bin",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["method"] == "MultipartHttpPushUri"
    assert result.data["target"] == MULTIPART_PUSH_TARGET
    assert result.data["image_file"] == "firmware.bin"
    assert result.data["blocked"] == "destructive update requires --confirm"
    assert _post_requests(redfish_service) == []


def test_firmware_update_confirm_posts_to_multipart_push_uri_on_gb300(
    tmp_path, redfish_service, redfish_mock
):
    """firmware-update --confirm POSTs only to the discovered push URI."""
    _set_update_service(redfish_service, _gb300_update_service())
    image = tmp_path / "firmware.bin"
    image.write_bytes(b"fw-bytes")

    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_file=str(image),
        confirm=True,
    )

    posts = _post_requests(redfish_service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["method"] == "MultipartHttpPushUri"
    assert result.data["target"] == MULTIPART_PUSH_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == MULTIPART_PUSH_TARGET.lower()
    assert b"fw-bytes" in posts[0].body
    assert b'name="UpdateFile"; filename="firmware.bin"' in posts[0].body


def test_firmware_update_refuses_when_no_update_path_exists(redfish_service, redfish_mock):
    """firmware-update reports a clean error when no update mechanism is exposed."""
    _set_update_service(redfish_service, {
        "@odata.id": "/redfish/v1/UpdateService",
        "Id": "UpdateService",
        "Actions": {},
    })

    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_file="firmware.bin",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "action": "firmware-update",
        "available": [],
        "update_service": "/redfish/v1/UpdateService",
    }
    assert "SimpleUpdate, MultipartHttpPushUri, or HttpPushUri" in result.error
    assert _post_requests(redfish_service) == []
