"""Dual-mode tests for the volume-init command (Volume.Initialize).

volume-init discovers a Volume's ``#Volume.Initialize`` action and POSTs a Fast
initialize request. The POST realizes as a task, so the vendor-faithful tests
assert the returned task id matches what each vendor's mock returns: a Dell OEM
``JID_`` job vs a plain DMTF TaskService id for every other vendor (the mock's
``service.JOB_ID`` moves with the vendor). Runs offline by default and against
real hardware when IDRAC_IP is set.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"
_VOLUME_ID = f"Disk.Virtual.0:{_CONTROLLER}"


def _volumes_path(mgr):
    """Return the controller Volumes collection path for the resolved system.

    :param mgr: the mock IDracManager whose system path is resolved lazily.
    :return: the ``{system}/Storage/{controller}/Volumes`` URI.
    """
    return f"{mgr.idrac_manage_servers}/Storage/{_CONTROLLER}/Volumes"


def _seed_initialize(service, volumes_path):
    """Seed a Volumes collection member advertising ``#Volume.Initialize``.

    :param service: the MockRedfishService whose overlay is seeded.
    :param volumes_path: the Volumes collection URI to seed under.
    :return: the Initialize action target URI the command should POST to.
    """
    init_target = f"{volumes_path}/{_VOLUME_ID}/Actions/Volume.Initialize"
    service._overlay[volumes_path.lower()] = {
        "@odata.id": volumes_path,
        "Members@odata.count": 1,
        "Members": [
            {
                "@odata.id": f"{volumes_path}/{_VOLUME_ID}",
                "Id": _VOLUME_ID,
                "Name": "Mock RAID volume",
                "Actions": {
                    "#Volume.Initialize": {
                        "target": init_target,
                        "InitializeType@Redfish.AllowableValues": ["Fast", "Slow"],
                    }
                },
            }
        ],
    }
    return init_target


@pytest.mark.parametrize("vendor", ["dell", "supermicro"])
def test_volume_init_realizes_task_faithfully_per_vendor(redfish_mock_factory, vendor):
    """volume-init POSTs Fast Initialize and returns the vendor-faithful task id.

    Dell realizes the action as an OEM ``JID_`` job; Supermicro (and any non-Dell
    vendor) as a DMTF TaskService id. Asserting ``task_id == service.JOB_ID`` on
    each proves the command reads the id the chokepoint decoded, not a hardcoded
    Dell literal.
    """
    mgr, service = redfish_mock_factory(vendor)
    init_target = _seed_initialize(service, _volumes_path(mgr))

    result = mgr.sync_invoke(
        ApiRequestType.VolumeInit,
        "chassis_service_query",
        dev_id=_CONTROLLER,
        vol_id=_VOLUME_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == service.JOB_ID
    posts = [request for request in service.requests if request.method == "POST"]
    assert len(posts) == 1
    assert posts[0].path.lower() == init_target.lower()
    assert posts[0].json() == {"InitializeType": "Fast"}


def test_volume_init_without_initialize_action_raises_without_post(redfish_mock_factory):
    """volume-init rejects a Volumes collection lacking Initialize metadata.

    When no member advertises ``#Volume.Initialize`` the command must raise
    UnsupportedAction before issuing any POST, so a mis-configured controller
    never triggers a mutation.
    """
    mgr, service = redfish_mock_factory("dell")
    volumes_path = _volumes_path(mgr)
    service._overlay[volumes_path.lower()] = {
        "@odata.id": volumes_path,
        "Members@odata.count": 1,
        "Members": [
            {"@odata.id": f"{volumes_path}/{_VOLUME_ID}", "Id": _VOLUME_ID}
        ],
    }

    with pytest.raises(UnsupportedAction, match="doesn't support this action"):
        mgr.sync_invoke(
            ApiRequestType.VolumeInit,
            "chassis_service_query",
            dev_id=_CONTROLLER,
            vol_id=_VOLUME_ID,
        )

    assert all(request.method != "POST" for request in service.requests)
