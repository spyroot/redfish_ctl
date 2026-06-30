"""Dual-mode tests for storage collection and volume collection commands."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"
_VOLUMES_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    f"{_CONTROLLER}/Volumes"
)


def test_storage_list_returns_storage_collection(redfish_api):
    """storage_list fetches the iDRAC system storage collection."""
    result = redfish_api.sync_invoke(
        ApiRequestType.StorageListQuery,
        "storage_list",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1/Storage"
    assert result.data["Members"][0]["@odata.id"].endswith("/RAID.Integrated.1-1")


def test_storage_query_filters_controller_ids(redfish_api):
    """storage_query returns filtered controller IDs and matching Redfish URIs."""
    result = redfish_api.sync_invoke(
        ApiRequestType.StorageQuery,
        "storage_query",
        id_filter="RAID",
    )

    assert isinstance(result, CommandResult)
    assert result.data == ["RAID.Integrated.1-1"]
    assert result.discovered == [
        "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.Integrated.1-1"
    ]


def test_volume_query_returns_controller_volume_collection(redfish_api):
    """vol_query fetches the volume collection under the selected controller."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VolumeQuery,
        "vol_query",
        dev_id="RAID.Integrated.1-1",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.Integrated.1-1/Volumes"
    )
    assert result.data["Members"][0]["@odata.id"].endswith(
        "/Volumes/Disk.Virtual.0:RAID.Integrated.1-1"
    )
    assert result.discovered == {}


def test_virtual_disk_query_returns_none_for_empty_volume_collection(
    redfish_mock,
    redfish_service,
):
    """volumes reads the selected Volumes collection without issuing a POST."""
    redfish_service._overlay[_VOLUMES_PATH.lower()] = {
        "@odata.id": _VOLUMES_PATH,
        "Members": [],
        "Members@odata.count": 0,
    }

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualDiskQuery,
        "virtual_disk_query",
        device_id=_CONTROLLER,
    )

    assert isinstance(result, CommandResult)
    assert result.data is None
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    get_paths = [request.path.lower() for request in redfish_service.requests
                 if request.method == "GET"]
    assert _VOLUMES_PATH.lower() in get_paths
    assert all(request.method != "POST" for request in redfish_service.requests)
