"""Vendor-faithful task realization for the manager_reset async command.

manager_reset POSTs ``Manager.Reset``; a BMC realizes that Action as a task and
answers 202 + a Location header at the new task. The task id is VENDOR-FAITHFUL:
a Dell BMC returns its OEM ``JID_`` job, every other vendor returns a plain DMTF
TaskService id. These tests assert the command surfaces whichever id the vendor
actually returned, so a Dell ``JID_`` literal is never assumed cross-vendor.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.idrac_shared import ApiRequestType, JobState
from redfish_ctl.manager.cmd_manager_reset import ManagerReset
from redfish_ctl.redfish_manager import CommandResult


def test_manager_reset_realizes_dell_oem_job(
    redfish_mock, redfish_service, monkeypatch
):
    """manager_reset surfaces Dell's OEM JID_ job id from the reset task."""

    def fetch_task(self, task_id):
        """Assert the polled id is the Dell OEM job and report it terminal."""
        assert task_id == redfish_service.JOB_ID
        return JobState.Completed

    monkeypatch.setattr(ManagerReset, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(ApiRequestType.ManagerReset, "manager_reset")

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_id"].startswith("JID_")
    assert result.data["task_state"] == JobState.Completed


def test_manager_reset_realizes_generic_dmtf_task(
    redfish_mock_factory, monkeypatch
):
    """manager_reset surfaces a plain DMTF task id on a non-Dell vendor."""
    manager, service = redfish_mock_factory("supermicro")

    def fetch_task(self, task_id):
        """Assert the polled id is the DMTF task and report it terminal."""
        assert task_id == service.JOB_ID
        return JobState.Completed

    monkeypatch.setattr(ManagerReset, "fetch_task", fetch_task)

    result = manager.sync_invoke(ApiRequestType.ManagerReset, "manager_reset")

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == service.JOB_ID
    assert not result.data["task_id"].startswith("JID_")
    assert result.data["task_state"] == JobState.Completed
