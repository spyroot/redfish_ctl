"""Dual-mode tests for the wait command."""

import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_wait_dualmode_returns_reachable_service_root(redfish_mock):
    """wait polls the Redfish ServiceRoot and returns a serializable result."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.WaitReady,
        "wait",
        wait_timeout=5,
        wait_interval=0,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["reachable"] is True
    assert result.data["target"] == redfish_mock.idrac_ip
    assert result.data["waiting_for"].endswith("/redfish/v1/")
    assert isinstance(result.data["waited_s"], float)
    assert result.data["waited_s"] >= 0.0
    assert "went_down" not in result.data
    json.dumps(result.data, sort_keys=True)


def test_wait_mock_mode_uses_only_service_root_get(redfish_mock, redfish_service):
    """wait is read-only in mock mode and only probes the ServiceRoot."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.WaitReady,
        "wait",
        wait_timeout=5,
        wait_interval=0,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["reachable"] is True
    assert isinstance(result.data["waited_s"], float)
    assert result.data["waited_s"] >= 0.0
    assert "went_down" not in result.data

    assert redfish_service.requests
    assert all(request.method == "GET" for request in redfish_service.requests)
    assert [request.path.lower() for request in redfish_service.requests] == [
        "/redfish/v1/"
    ]
