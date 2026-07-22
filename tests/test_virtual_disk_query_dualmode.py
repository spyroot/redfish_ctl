"""Dual-mode tests for the storage virtual-disk query command."""
import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"
_VOLUMES_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    f"{_CONTROLLER}/Volumes"
)
_PRESENT_VOLUME_ID = f"Disk.Virtual.0:{_CONTROLLER}"
_MISSING_VOLUME_ID = f"Disk.Virtual.1:{_CONTROLLER}"
_PRESENT_VOLUME_QUERY_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    f"Volumes/{_PRESENT_VOLUME_ID}"
)
_MISSING_VOLUME_QUERY_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    f"Volumes/{_MISSING_VOLUME_ID}"
)


def _seed_volume_collection(service, *volume_ids):
    """Seed a Volumes collection with the requested member identifiers."""
    service._overlay[_VOLUMES_PATH.lower()] = {
        "@odata.id": _VOLUMES_PATH,
        "Members@odata.count": len(volume_ids),
        "Members": [
            {"@odata.id": f"{_VOLUMES_PATH}/{volume_id}"}
            for volume_id in volume_ids
        ],
    }


def _seed_present_volume(service):
    """Seed the per-volume path that virtual_disk_query fetches."""
    service._overlay[_PRESENT_VOLUME_QUERY_PATH.lower()] = {
        "@odata.id": _PRESENT_VOLUME_QUERY_PATH,
        "Id": _PRESENT_VOLUME_ID,
        "Name": "Mock virtual disk",
        "CapacityBytes": 536870912000,
        "Status": {"State": "Enabled", "Health": "OK"},
    }


def test_virtual_disk_query_returns_per_volume_dicts(
    redfish_mock,
    redfish_service,
):
    """virtual_disk_query expands volume ids into per-volume resource dicts."""
    _seed_volume_collection(redfish_service, _PRESENT_VOLUME_ID)
    _seed_present_volume(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualDiskQuery,
        "virtual_disk_query",
        device_id=_CONTROLLER,
    )

    assert isinstance(result, CommandResult)
    json.dumps(result.data)
    assert result.data == [
        {
            "@odata.id": _PRESENT_VOLUME_QUERY_PATH,
            "Id": _PRESENT_VOLUME_ID,
            "Name": "Mock virtual disk",
            "CapacityBytes": 536870912000,
            "Status": {"State": "Enabled", "Health": "OK"},
        }
    ]
    assert all(isinstance(volume, dict) for volume in result.data)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None


def test_virtual_disk_query_warns_and_continues_when_volume_get_404(
    redfish_mock,
    redfish_service,
):
    """A missing member resource warns and does not abort the collection walk."""
    _seed_volume_collection(redfish_service, _PRESENT_VOLUME_ID, _MISSING_VOLUME_ID)
    _seed_present_volume(redfish_service)

    with pytest.warns(UserWarning):
        result = redfish_mock.sync_invoke(
            ApiRequestType.VirtualDiskQuery,
            "virtual_disk_query",
            device_id=_CONTROLLER,
        )

    assert isinstance(result, CommandResult)
    assert [volume["Id"] for volume in result.data] == [_PRESENT_VOLUME_ID]
    get_paths = [
        request.path.lower()
        for request in redfish_service.requests
        if request.method == "GET"
    ]
    assert _PRESENT_VOLUME_QUERY_PATH.lower() in get_paths
    assert _MISSING_VOLUME_QUERY_PATH.lower() in get_paths


def test_virtual_disk_query_without_device_id_lists_available_controllers(
    redfish_api,
):
    """Missing device_id raises InvalidArgument with available controller ids."""
    with pytest.raises(InvalidArgument) as exc_info:
        redfish_api.sync_invoke(
            ApiRequestType.VirtualDiskQuery,
            "virtual_disk_query",
        )

    message = str(exc_info.value)
    assert "Storage device_id None not found" in message
    assert _CONTROLLER in message
