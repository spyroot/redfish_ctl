"""Dual-mode tests for storage collection and volume collection commands."""
import json

import pytest

from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"
_VOLUMES_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    f"{_CONTROLLER}/Volumes"
)
_VOLUME_ID = f"Disk.Virtual.0:{_CONTROLLER}"
_VOLUME_PATH = f"{_VOLUMES_PATH}/{_VOLUME_ID}"
_VOLUME_INIT_PATH = f"{_VOLUME_PATH}/Actions/Volume.Initialize"


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _seed_volume_initialize_action(service):
    """Seed an expanded Volumes collection member with Initialize action metadata."""
    service._overlay[_VOLUMES_PATH.lower()] = {
        "@odata.id": _VOLUMES_PATH,
        "Members@odata.count": 1,
        "Members": [
            {
                "@odata.id": _VOLUME_PATH,
                "Id": _VOLUME_ID,
                "Name": "Mock RAID volume",
                "Actions": {
                    "#Volume.Initialize": {
                        "target": _VOLUME_INIT_PATH,
                        "InitializeType@Redfish.AllowableValues": ["Fast", "Slow"],
                    }
                },
            }
        ],
    }


def _assert_volume_init_post(service):
    """Assert VolumeInit sent the expected Initialize request."""
    posts = _post_requests(service)
    assert len(posts) == 1
    request = posts[0]
    assert request.path.lower() == _VOLUME_INIT_PATH.lower()
    assert request.json() == {"InitializeType": "Fast"}


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


def test_storage_query_without_filter_returns_all_controller_ids(
    redfish_mock, redfish_service
):
    """storage_query without a filter returns every controller ID without mutating."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.StorageQuery,
        "storage_query",
        id_filter="",
    )

    assert isinstance(result, CommandResult)
    json.dumps({"data": result.data, "discovered": result.discovered})
    assert result.data == ["RAID.Integrated.1-1"]
    assert result.discovered == [
        "/redfish/v1/Systems/System.Embedded.1/Storage/RAID.Integrated.1-1"
    ]
    assert result.extra is None
    assert result.error is None

    assert {request.method for request in redfish_service.requests} == {"GET"}
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/storage"
    )


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


def test_volume_query_discovers_initialize_action_from_expanded_member(
    redfish_mock, redfish_service
):
    """vol_query discovers Initialize when the expanded member advertises it."""
    _seed_volume_initialize_action(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.VolumeQuery,
        "vol_query",
        dev_id=_CONTROLLER,
    )

    assert isinstance(result, CommandResult)
    assert result.discovered["Initialize"].target == _VOLUME_INIT_PATH
    assert _post_requests(redfish_service) == []


def test_volume_init_without_initialize_action_raises_without_post(
    redfish_mock, redfish_service
):
    """volume-init rejects a volume collection that lacks Initialize metadata."""
    with pytest.raises(UnsupportedAction, match="doesn't support this action"):
        redfish_mock.sync_invoke(
            ApiRequestType.VolumeInit,
            "chassis_service_query",
            dev_id=_CONTROLLER,
            vol_id=_VOLUME_ID,
        )

    assert _post_requests(redfish_service) == []


def test_volume_init_posts_fast_initialize_to_discovered_action_in_mock_mode(
    redfish_mock, redfish_service
):
    """volume-init builds a Fast Initialize request from the discovered action."""
    _seed_volume_initialize_action(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.VolumeInit,
        "chassis_service_query",
        dev_id=_CONTROLLER,
        vol_id=_VOLUME_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    _assert_volume_init_post(redfish_service)
