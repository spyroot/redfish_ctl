"""Dual-mode tests for the manager reset command."""

from redfish_ctl.idrac_shared import ApiRequestType, JobState
from redfish_ctl.manager.cmd_manager_reset import ManagerReset
from redfish_ctl.redfish_manager import CommandResult


def test_manager_reset_posts_graceful_restart_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """manager_reset POSTs the graceful reset action and records the job state."""

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return JobState.Completed

    monkeypatch.setattr(ManagerReset, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ManagerReset, "manager_reset"
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == JobState.Completed

    reset_requests = [
        request
        for request in redfish_service.requests
        if request.path.lower().endswith("/actions/manager.reset")
    ]
    assert len(reset_requests) == 1
    request = reset_requests[0]
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/managers/idrac.embedded.1/actions/manager.reset"
    )
    assert request.json() == {"ResetType": "GracefulRestart"}


def test_manager_reset_wait_posts_reset_and_attaches_reachability(
    redfish_mock, redfish_service, monkeypatch
):
    """manager_reset --wait records the ServiceRoot wait result after reset."""
    wait_calls = []

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return JobState.Completed

    def wait_reachable(url, auth, verify, timeout, interval, reboot_cycle):
        wait_calls.append(
            {
                "url": url,
                "auth": auth,
                "verify": verify,
                "timeout": timeout,
                "interval": interval,
                "reboot_cycle": reboot_cycle,
            }
        )
        return {"reachable": True, "went_down": True, "waited_s": 0.25}

    monkeypatch.setattr(ManagerReset, "fetch_task", fetch_task)
    monkeypatch.setattr("idrac_ctl.cmd_wait.wait_reachable", wait_reachable)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ManagerReset,
        "manager_reset",
        do_wait=True,
        wait_timeout=12.5,
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == JobState.Completed
    assert result.data["wait"] == {
        "reachable": True,
        "went_down": True,
        "waited_s": 0.25,
    }
    assert wait_calls == [
        {
            "url": "https://mock-idrac:443/redfish/v1/",
            "auth": ("root", "mock"),
            "verify": False,
            "timeout": 12.5,
            "interval": 5.0,
            "reboot_cycle": True,
        }
    ]

    reset_requests = [
        request
        for request in redfish_service.requests
        if request.path.lower().endswith("/actions/manager.reset")
    ]
    assert len(reset_requests) == 1
    assert reset_requests[0].method == "POST"
    assert reset_requests[0].json() == {"ResetType": "GracefulRestart"}
