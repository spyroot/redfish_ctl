"""Dual-mode tests for the guarded system-reset command."""

import pytest

from redfish_ctl.command_shared import ApiRequestType, Singleton
from redfish_ctl.compute.cmd_system_reset import SystemReset
from redfish_ctl.redfish_manager import CommandResult

RESET_TARGET = (
    "/redfish/v1/systems/system.embedded.1/actions/computersystem.reset"
)


@pytest.fixture(autouse=True)
def reset_system_reset_singleton():
    """Keep cached command state from leaking across vendor-shaped tests."""
    Singleton._instances.pop(SystemReset, None)
    yield
    Singleton._instances.pop(SystemReset, None)


def _post_requests(redfish_service):
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def test_system_reset_defaults_to_dry_run_in_mock_mode(
    redfish_mock, redfish_service
):
    """system-reset previews the Dell ComputerSystem.Reset action by default."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.SystemReset,
        "system_reset",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#ComputerSystem.Reset"
    assert result.data["target"] == (
        "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
    )
    assert result.data["payload"] == {"ResetType": "GracefulRestart"}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(redfish_service) == []


def test_system_reset_confirm_posts_reset_payload_in_mock_mode(
    redfish_mock, redfish_service
):
    """system-reset --confirm POSTs the requested ResetType to the Dell target."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.SystemReset,
        "system_reset",
        reset_type="ForceRestart",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
    )
    assert result.data["level"] == "destructive"

    reset_requests = [
        request
        for request in _post_requests(redfish_service)
        if request.path.lower() == RESET_TARGET
    ]
    assert len(reset_requests) == 1
    request = reset_requests[0]
    assert request.json() == {"ResetType": "ForceRestart"}
