"""Dual-mode tests for guarded Redfish volume create and delete commands."""
import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument, UnsupportedAction
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"
_STORAGE_PATH = f"/redfish/v1/Systems/System.Embedded.1/Storage/{_CONTROLLER}"
_VOLUMES_PATH = f"{_STORAGE_PATH}/Volumes"
_DRIVE_PATH = f"{_STORAGE_PATH}/Drives/Disk.Bay.0"
_VOLUME_ID = f"Disk.Virtual.0:{_CONTROLLER}"
_VOLUME_PATH = f"{_VOLUMES_PATH}/{_VOLUME_ID}"


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _delete_requests(service):
    """Return DELETE requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "DELETE"]


def _seed_raid_capabilities(service, values=("RAID0", "RAID1")):
    """Seed advertised RAID types so create validates controller metadata."""
    storage = dict(service._state(_STORAGE_PATH.lower()))
    storage["SupportedRAIDTypes"] = list(values)
    service._overlay[_STORAGE_PATH.lower()] = storage


def _invoke_create(redfish_mock, **kwargs):
    """Invoke volume-create with defaults used across guarded-create tests."""
    params = {
        "controller": _CONTROLLER,
        "volume_name": "os-mirror",
        "raid_type": "RAID1",
        "drives": ["Disk.Bay.0"],
    }
    params.update(kwargs)
    return redfish_mock.sync_invoke(
        ApiRequestType.VolumeCreate,
        "volume-create",
        **params,
    )


def _invoke_delete(redfish_mock, **kwargs):
    """Invoke volume-delete with defaults used across guarded-delete tests."""
    params = {
        "controller": _CONTROLLER,
        "volume_id": _VOLUME_ID,
    }
    params.update(kwargs)
    return redfish_mock.sync_invoke(
        ApiRequestType.VolumeDelete,
        "volume-delete",
        **params,
    )


def test_volume_create_dry_run_resolves_payload_without_post(
    redfish_mock,
    redfish_service,
):
    """volume-create previews the standard Volumes POST without mutating."""
    _seed_raid_capabilities(redfish_service)

    result = _invoke_create(redfish_mock)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "create",
        "target": _VOLUMES_PATH,
        "payload": {
            "Name": "os-mirror",
            "RAIDType": "RAID1",
            "Links": {"Drives": [{"@odata.id": _DRIVE_PATH}]},
        },
        "hint": "re-run with --confirm to create the volume",
    }
    assert _post_requests(redfish_service) == []


def test_volume_create_confirm_posts_standard_payload(redfish_mock, redfish_service):
    """volume-create --confirm POSTs a vendor-neutral Volume payload."""
    _seed_raid_capabilities(redfish_service)

    result = _invoke_create(redfish_mock, confirm=True)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["status"]
    posts = _post_requests(redfish_service)
    assert len(posts) == 1
    assert posts[0].path.lower() == _VOLUMES_PATH.lower()
    assert posts[0].json() == {
        "Name": "os-mirror",
        "RAIDType": "RAID1",
        "Links": {"Drives": [{"@odata.id": _DRIVE_PATH}]},
    }


def test_volume_create_rejects_unsupported_raid_type_without_post(
    redfish_mock,
    redfish_service,
):
    """volume-create validates RAIDType when the controller advertises a set."""
    _seed_raid_capabilities(redfish_service, values=("RAID0",))

    with pytest.raises(InvalidArgument, match="RAID1"):
        _invoke_create(redfish_mock)

    assert _post_requests(redfish_service) == []


def test_volume_create_refuses_controller_without_volumes_link(
    redfish_mock,
    redfish_service,
):
    """volume-create fails closed when a Storage resource has no Volumes link."""
    storage = dict(redfish_service._state(_STORAGE_PATH.lower()))
    storage.pop("Volumes")
    redfish_service._overlay[_STORAGE_PATH.lower()] = storage

    with pytest.raises(UnsupportedAction, match="Volumes"):
        _invoke_create(redfish_mock)

    assert _post_requests(redfish_service) == []


def test_volume_delete_dry_run_requires_no_delete(redfish_mock, redfish_service):
    """volume-delete previews the member URI and writes nothing by default."""
    result = _invoke_delete(redfish_mock)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "delete",
        "target": _VOLUME_ID,
        "uri": _VOLUME_PATH,
        "hint": "re-run with --confirm and --confirm_volume_id to delete",
    }
    assert _delete_requests(redfish_service) == []


def test_volume_delete_confirm_requires_matching_volume_id(
    redfish_mock,
    redfish_service,
):
    """volume-delete --confirm requires the volume id to be typed again."""
    result = _invoke_delete(
        redfish_mock,
        confirm=True,
        confirm_volume_id="wrong-volume",
    )

    assert result.error == f"confirm_volume_id must match {_VOLUME_ID}"
    assert _delete_requests(redfish_service) == []


def test_volume_delete_confirm_deletes_member(redfish_mock, redfish_service):
    """volume-delete --confirm deletes the resolved Volume member URI."""
    result = _invoke_delete(
        redfish_mock,
        confirm=True,
        confirm_volume_id=_VOLUME_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "delete"
    deletes = _delete_requests(redfish_service)
    assert len(deletes) == 1
    assert deletes[0].path.lower() == _VOLUME_PATH.lower()


def test_volume_delete_unknown_volume_lists_available_ids(redfish_mock):
    """volume-delete rejects unknown ids and reports the available Volumes."""
    with pytest.raises(InvalidArgument, match=_VOLUME_ID):
        _invoke_delete(redfish_mock, volume_id="missing-volume")


@pytest.mark.live
def test_volume_create_live_preview_only(redfish_api):
    """Live mode keeps volume-create read-only unless an operator confirms."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VolumeCreate,
        "volume-create",
        controller=_CONTROLLER,
        volume_name="preview-only",
        raid_type="RAID1",
        drives=["Disk.Bay.0"],
    )
    assert isinstance(result, CommandResult)
    json.dumps(result.data)
    assert result.data["dry_run"] is True
