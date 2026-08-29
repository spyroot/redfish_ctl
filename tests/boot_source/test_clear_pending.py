"""Dell tests for the clear-pending boot-source action (BootOptionsClearPending).

    redfish_ctl boot-options-clear

Covers ``clear_pending`` (ApiRequestType.BootOptionsClearPending), a POST to the
Dell OEM ``DellManager.ClearPending`` action. The action realizes as a task, so
the mock returns 202 + a Dell OEM ``JID_`` job. Non-Dell managers must not
register this OEM command.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.boot_source.cmd_clear_pending import BootOptionsClearPending
from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_clear_pending_realizes_dell_jid_job(redfish_mock, redfish_service, monkeypatch):
    """clear_pending POSTs the action and surfaces the Dell OEM JID_ job id."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        """Return a terminal task state without polling a real BMC."""
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(BootOptionsClearPending, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOptionsClearPending,
        "clear_pending",
    )
    assert isinstance(result, CommandResult)
    # Dell realizes the OEM job -> a JID_ id
    assert redfish_service.JOB_ID.startswith("JID_")
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower().endswith("actions/dellmanager.clearpending")
    assert request.json() == {}


def test_clear_pending_is_not_registered_on_supermicro(redfish_mock_factory):
    """A Supermicro manager cannot dispatch the DellManager OEM action."""
    manager, _ = redfish_mock_factory("supermicro")
    with pytest.raises(UnsupportedAction, match="Unknown clear_pending command"):
        manager.sync_invoke(
            ApiRequestType.BootOptionsClearPending,
            "clear_pending",
        )
