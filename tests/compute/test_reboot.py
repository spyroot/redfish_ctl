"""Dual-mode tests for the reboot command (ComputerSystem.Reset).

The ``reboot`` command (``compute/cmd_power_state.py``) POSTs the host
ComputerSystem's own ``#ComputerSystem.Reset`` action -- a pure DMTF action that
realizes as a task. These tests assert VENDOR-FAITHFUL task realization against
both a Dell tree (``JID_`` OEM job) and a Supermicro/generic tree (a plain DMTF
TaskService id), proving the command never bakes in the Dell ``JID_`` shape.
"""

import pytest

from redfish_ctl.compute.cmd_power_state import RebootHost
from redfish_ctl.idrac_shared import ApiRequestType, Singleton
from redfish_ctl.redfish_manager import CommandResult


@pytest.fixture(autouse=True)
def reset_reboot_singleton():
    """Drop cached RebootHost state so vendor-shaped tests do not leak host ids."""
    Singleton._instances.pop(RebootHost, None)
    yield
    Singleton._instances.pop(RebootHost, None)


def _post_requests(service):
    """Return only the POST requests the mock service recorded."""
    return [request for request in service.requests if request.method == "POST"]


def test_reboot_realizes_dell_jid_task_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """reboot surfaces the Dell JID_ job id returned by ComputerSystem.Reset."""
    captured = {}

    def fake_fetch(self, task_id):
        """Record the polled task id and return a terminal task state."""
        captured["task_id"] = task_id
        return {"TaskState": "Completed", "TaskStatus": "OK"}

    monkeypatch.setattr(RebootHost, "fetch_task", fake_fetch)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type="GracefulRestart",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "#ComputerSystem.Reset"
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.JOB_ID.startswith("JID_")
    assert captured["task_id"] == redfish_service.JOB_ID
    assert len(_post_requests(redfish_service)) == 1


def test_reboot_realizes_dmtf_task_on_supermicro(
    redfish_mock_factory, monkeypatch
):
    """reboot surfaces a plain DMTF TaskService id on a Supermicro tree, not JID_."""
    captured = {}

    def fake_fetch(self, task_id):
        """Record the polled task id and return a terminal task state."""
        captured["task_id"] = task_id
        return {"TaskState": "Completed", "TaskStatus": "OK"}

    monkeypatch.setattr(RebootHost, "fetch_task", fake_fetch)

    manager, service = redfish_mock_factory("supermicro")
    result = manager.sync_invoke(
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type="GracefulRestart",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "#ComputerSystem.Reset"
    assert result.data["task_id"] == service.JOB_ID
    assert not service.JOB_ID.startswith("JID_")
    assert captured["task_id"] == service.JOB_ID
    assert len(_post_requests(service)) == 1


def test_reboot_dry_run_previews_without_posting(redfish_mock, redfish_service):
    """reboot --dry_run resolves the reset target and POSTs nothing."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type="GracefulRestart",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#ComputerSystem.Reset"
    assert result.data["payload"] == {"ResetType": "GracefulRestart"}
    assert _post_requests(redfish_service) == []
