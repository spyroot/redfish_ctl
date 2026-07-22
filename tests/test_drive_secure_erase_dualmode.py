"""Dual-mode tests for guarded Drive.SecureErase."""
import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType

_CONTROLLER = "RAID.Integrated.1-1"
_STORAGE_PATH = f"/redfish/v1/Systems/System.Embedded.1/Storage/{_CONTROLLER}"
_DRIVE_ID = "Disk.Bay.0"
_DRIVE_PATH = f"{_STORAGE_PATH}/Drives/{_DRIVE_ID}"
_CHASSIS_DRIVE_PATH = "/redfish/v1/Chassis/NVME_M2_0/Drives/NVMe_SSD_210"
_SECURE_ERASE_TARGET = f"{_DRIVE_PATH}/Actions/Drive.SecureErase"
_CHASSIS_SECURE_ERASE_TARGET = f"{_CHASSIS_DRIVE_PATH}/Actions/Drive.SecureErase"


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _seed_drive(service, uri=_DRIVE_PATH, target=_SECURE_ERASE_TARGET):
    """Seed a Drive resource advertising SecureErase."""
    service._overlay[uri.lower()] = {
        "@odata.id": uri,
        "@odata.type": "#Drive.v1_16_0.Drive",
        "Id": uri.rstrip("/").split("/")[-1],
        "Name": "Mock drive",
        "Actions": {
            "#Drive.SecureErase": {
                "target": target,
            }
        },
    }


def _invoke(redfish_mock, **kwargs):
    """Invoke drive-secure-erase through the command registry."""
    params = {"controller": _CONTROLLER}
    params.update(kwargs)
    return redfish_mock.sync_invoke(
        ApiRequestType.DriveSecureErase,
        "drive-secure-erase",
        **params,
    )


def test_drive_secure_erase_lists_controller_drives_without_post(
    redfish_mock,
    redfish_service,
):
    """With no drive id, the command lists capable drives and stays read-only."""
    _seed_drive(redfish_service)

    result = _invoke(redfish_mock)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "controller": _CONTROLLER,
        "drives": [
            {
                "drive_id": _DRIVE_ID,
                "uri": _DRIVE_PATH,
                "secure_erase": True,
                "target": _SECURE_ERASE_TARGET,
            }
        ],
        "hint": "pass --drive_id to preview Drive.SecureErase",
    }
    assert _post_requests(redfish_service) == []


def test_drive_secure_erase_dry_run_requires_irreversible_ack(
    redfish_mock,
    redfish_service,
):
    """Targeting a drive previews by default and does not POST."""
    _seed_drive(redfish_service)

    result = _invoke(redfish_mock, drive_id=_DRIVE_ID)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#Drive.SecureErase",
        "target": _SECURE_ERASE_TARGET,
        "payload": {},
        "level": "irreversible",
        "blocked": (
            "irreversible action requires --confirm and "
            "--i-understand-irreversible"
        ),
    }
    assert _post_requests(redfish_service) == []


def test_drive_secure_erase_confirm_posts_empty_payload(
    redfish_mock,
    redfish_service,
):
    """Both confirmation flags allow POSTing the discovered action target."""
    _seed_drive(redfish_service)

    result = _invoke(
        redfish_mock,
        drive_id=_DRIVE_ID,
        confirm=True,
        confirm_irreversible=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#Drive.SecureErase"
    posts = _post_requests(redfish_service)
    assert len(posts) == 1
    assert posts[0].path.lower() == _SECURE_ERASE_TARGET.lower()
    assert posts[0].json() == {}


def test_drive_secure_erase_supports_exact_drive_uri(redfish_mock, redfish_service):
    """Exact Drive URIs cover non-Storage paths such as chassis drive resources."""
    _seed_drive(
        redfish_service,
        uri=_CHASSIS_DRIVE_PATH,
        target=_CHASSIS_SECURE_ERASE_TARGET,
    )

    result = _invoke(
        redfish_mock,
        controller=None,
        drive_uri=_CHASSIS_DRIVE_PATH,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["target"] == _CHASSIS_SECURE_ERASE_TARGET
    assert _post_requests(redfish_service) == []


def test_drive_secure_erase_rejects_unknown_drive_without_post(
    redfish_mock,
    redfish_service,
):
    """Unknown controller-local drive ids report the available ids."""
    _seed_drive(redfish_service)

    with pytest.raises(InvalidArgument, match=_DRIVE_ID):
        _invoke(redfish_mock, drive_id="Disk.Bay.99")

    assert _post_requests(redfish_service) == []


def test_drive_secure_erase_rejects_ambiguous_target_flags(redfish_mock):
    """Exact URI mode cannot be combined with controller-relative targeting."""
    with pytest.raises(InvalidArgument, match="drive_uri"):
        _invoke(redfish_mock, drive_id=_DRIVE_ID, drive_uri=_CHASSIS_DRIVE_PATH)


@pytest.mark.live
def test_drive_secure_erase_live_preview_only(redfish_api):
    """Live mode keeps Drive.SecureErase read-only without confirmations."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DriveSecureErase,
        "drive-secure-erase",
        drive_uri=_CHASSIS_DRIVE_PATH,
    )
    assert isinstance(result, CommandResult)
    json.dumps(result.data)
    assert result.data["dry_run"] is True
