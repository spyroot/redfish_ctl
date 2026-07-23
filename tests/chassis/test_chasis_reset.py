"""Dual-mode tests for the chassis-reset command's task realization.

chassis-reset POSTs the DMTF ``#Chassis.Reset`` action, so a vendor that
realizes it as a task returns a job/task id in the response ``Location``.
These tests assert the realization is VENDOR-FAITHFUL against two vendors:
Dell returns its OEM ``JID_`` job id, while a non-Dell (Supermicro) box
returns a plain DMTF TaskService id (never ``JID_``). The mock never fabricates
a ``JID_`` for a non-Dell tree (see ``tests/conftest.py``), so the same command
code is proven to carry each vendor's id shape unchanged.

Offline only: the POST is served by the mock Redfish service and never
reaches real hardware.
"""

from redfish_ctl.chassis.cmd_chasis_reset import ChassisReset
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_TASK_STATE = {"TaskState": "Completed", "TaskStatus": "OK"}


def _seed_single_member(manager, service, base_url, member_path):
    """Overlay the Chassis collection with one member that has a Reset action.

    A read-only collection GET returns link-only members without an ``Actions``
    block, so the reset action is undiscoverable; the command's own test path
    seeds the expanded member. This fetches the member fixture and installs it
    as the sole collection member so ``discover_reset`` finds one Reset target.

    :param manager: the mock-backed Redfish manager under test.
    :param service: the backing ``MockRedfishService`` whose overlay is seeded.
    :param base_url: the ``https://host`` prefix for the manager's mock.
    :param member_path: the Chassis member path exposing ``#Chassis.Reset``.
    :return: None; the service overlay is mutated in place.
    """
    member_response = manager.api_get_call(f"{base_url}{member_path}", {})
    assert member_response.status_code == 200, f"missing fixture for {member_path}"

    collection_response = manager.api_get_call(f"{base_url}/redfish/v1/Chassis", {})
    assert collection_response.status_code == 200
    collection_path = service.last_request.path
    collection = collection_response.json()
    collection["Members"] = [member_response.json()]
    service._overlay[collection_path] = collection


def test_chassis_reset_realizes_dell_jid_job(
    redfish_mock, redfish_service, monkeypatch
):
    """chassis-reset carries Dell's OEM JID_ job id from the Reset response."""
    _seed_single_member(
        redfish_mock,
        redfish_service,
        "https://mock-idrac",
        "/redfish/v1/Chassis/System.Embedded.1",
    )

    def fetch_task(self, task_id):
        """Assert the polled id is the vendor's job id and return a terminal state."""
        assert task_id == redfish_service.JOB_ID
        return _TASK_STATE

    monkeypatch.setattr(ChassisReset, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ChassisReset, "reboot", reset_type="ForceOff"
    )

    assert isinstance(result, CommandResult)
    assert redfish_service.JOB_ID.startswith("JID_")
    assert result.data == {"task_id": redfish_service.JOB_ID, "task_state": _TASK_STATE}
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/Chassis/System.Embedded.1/Actions/Chassis.Reset".lower()
    )
    assert request.json() == {"ResetType": "ForceOff"}


def test_chassis_reset_realizes_supermicro_dmtf_task(
    redfish_mock_factory, monkeypatch
):
    """chassis-reset carries a plain DMTF task id (never JID_) for Supermicro."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_single_member(
        manager,
        service,
        "https://mock-supermicro",
        "/redfish/v1/Chassis/Chassis_0",
    )

    def fetch_task(self, task_id):
        """Assert the polled id is the vendor's task id and return a terminal state."""
        assert task_id == service.JOB_ID
        return _TASK_STATE

    monkeypatch.setattr(ChassisReset, "fetch_task", fetch_task)

    result = manager.sync_invoke(
        ApiRequestType.ChassisReset, "reboot", reset_type="ForceOff"
    )

    assert isinstance(result, CommandResult)
    assert not service.JOB_ID.startswith("JID_")
    assert result.data == {"task_id": service.JOB_ID, "task_state": _TASK_STATE}
    request = service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/Chassis/Chassis_0/Actions/Chassis.Reset".lower()
    )
    assert request.json() == {"ResetType": "ForceOff"}
