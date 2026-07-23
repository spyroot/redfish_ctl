"""Dual-mode tests for the volume-get command (vol_query).

vol_query is a pure read: it GETs the ``{system}/Storage/{controller}/Volumes``
collection (expanded) and returns the payload plus any discovered Redfish actions
on its members. The tests assert it never mutates, surfaces a member's
``#Volume.Initialize`` as a discovered action, and resolves the collection on a
generic (non-Dell) tree. Runs offline by default and against real hardware when
IDRAC_IP is set.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"


def test_volume_get_returns_collection_without_mutating(redfish_mock, redfish_service):
    """vol_query fetches the controller Volumes collection using only GETs.

    The Dell fixture exposes a Volumes collection under the controller; the
    command must return it as a dict and issue no PATCH/POST/DELETE.
    """
    result = redfish_mock.sync_invoke(
        ApiRequestType.VolumeQuery, "vol_query", dev_id=_CONTROLLER
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    assert str(result.data["@odata.id"]).endswith(f"/Storage/{_CONTROLLER}/Volumes")
    assert all(request.method == "GET" for request in redfish_service.requests)


def test_volume_get_discovers_initialize_action(redfish_mock, redfish_service):
    """vol_query surfaces a member's ``#Volume.Initialize`` as a discovered action.

    The discovered map is what volume-init later consumes, so vol_query must key
    the action by its short name (``Initialize``) with the advertised target.
    """
    volumes_path = (
        f"/redfish/v1/Systems/System.Embedded.1/Storage/{_CONTROLLER}/Volumes"
    )
    vol_id = f"Disk.Virtual.0:{_CONTROLLER}"
    init_target = f"{volumes_path}/{vol_id}/Actions/Volume.Initialize"
    redfish_service._overlay[volumes_path.lower()] = {
        "@odata.id": volumes_path,
        "Members@odata.count": 1,
        "Members": [
            {
                "@odata.id": f"{volumes_path}/{vol_id}",
                "Id": vol_id,
                "Actions": {"#Volume.Initialize": {"target": init_target}},
            }
        ],
    }

    result = redfish_mock.sync_invoke(
        ApiRequestType.VolumeQuery, "vol_query", dev_id=_CONTROLLER
    )

    assert result.discovered["Initialize"].target == init_target
    assert all(request.method == "GET" for request in redfish_service.requests)


def test_volume_get_vendor_neutral_generic(redfish_mock_factory):
    """vol_query resolves the Volumes collection on a generic (non-Dell) tree.

    A read must be vendor-neutral; against a seeded generic tree the command
    returns the empty collection it was given and issues no mutation.
    """
    mgr, service = redfish_mock_factory("generic")
    volumes_path = f"{mgr.idrac_manage_servers}/Storage/{_CONTROLLER}/Volumes"
    service._overlay[volumes_path.lower()] = {
        "@odata.id": volumes_path,
        "Members": [],
        "Members@odata.count": 0,
    }

    result = mgr.sync_invoke(
        ApiRequestType.VolumeQuery, "vol_query", dev_id=_CONTROLLER
    )

    assert isinstance(result, CommandResult)
    assert result.data["@odata.id"] == volumes_path
    assert all(request.method == "GET" for request in service.requests)
