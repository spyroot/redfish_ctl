"""Dual-mode tests for the serial-console command (report + guarded enable).

Read/status is non-mutating; --enable previews unless --confirm is given. These run
offline against the mock and assert that neither path sends a PATCH without --confirm.
"""
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.serial_console.cmd_serial_console import (  # noqa: F401
    SerialConsoleConfig,
)


def _unwrap(result):
    """sync_invoke may wrap the CommandResult once; return the payload dict."""
    data = result.data
    if isinstance(data, CommandResult):
        data = data.data
    return data


def _mutating(redfish_service):
    return [r for r in redfish_service.requests
            if r.method in {"POST", "PATCH", "DELETE"}]


def test_serial_console_status_reads_without_mutation(redfish_mock, redfish_service):
    """serial-console (no --enable) reports state and sends no mutating request."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.SerialConsoleConfig, "serial-console"
    )

    assert isinstance(result, CommandResult)
    data = _unwrap(result)
    assert isinstance(data, dict)
    for key in ("System", "BiosSerialAttribute", "BiosSerialCurrent", "Sol"):
        assert key in data
    assert isinstance(data["Sol"], list)
    assert _mutating(redfish_service) == []


def test_serial_console_enable_dry_run_plans_without_patch(redfish_mock, redfish_service):
    """--enable without --confirm returns a plan and sends no PATCH."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.SerialConsoleConfig, "serial-console",
        enable=True, confirm=False,
        bios_attr="SerialComm", bios_value="OnConRedirCom2",
    )

    data = _unwrap(result)
    assert data["dry_run"] is True
    assert data["plan"]["bios"]["payload"] == {
        "Attributes": {"SerialComm": "OnConRedirCom2"}
    }
    assert data["plan"]["bios"]["target"].endswith("/Bios/Settings")
    assert _mutating(redfish_service) == []


def test_serial_console_enable_confirm_patches_bios_and_skips_enabled_sol(
    redfish_mock, redfish_service
):
    """--enable --confirm stages BIOS serial redirection without repatching enabled SOL."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.SerialConsoleConfig,
        "serial-console",
        enable=True,
        confirm=True,
    )

    data = _unwrap(result)
    assert data["plan"]["bios"]["payload"] == {
        "Attributes": {"SerialComm": "OnConRedirCom2"}
    }
    assert data["plan"]["sol"] == []
    assert data["plan"]["sol_already_enabled"] == ["iDRAC.Embedded.1"]
    assert data["status_before"]["BiosSerialAttribute"] == "SerialComm"
    assert data["status_before"]["BiosSerialCurrent"] == "Off"

    mutating_requests = _mutating(redfish_service)
    assert len(mutating_requests) == 1
    bios_patch = mutating_requests[0]
    assert bios_patch.method == "PATCH"
    assert bios_patch.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/bios/settings"
    )
    assert bios_patch.json() == {
        "Attributes": {"SerialComm": "OnConRedirCom2"}
    }
    assert data["applied"]["bios"]["Status"] == "ok"
    assert data["applied"]["sol"] == []


def test_serial_console_enable_needs_value_for_unknown_attr(redfish_mock, redfish_service):
    """An unknown BIOS attribute with no --bios_value is rejected before any write."""
    import pytest

    with pytest.raises(Exception):
        redfish_mock.sync_invoke(
            ApiRequestType.SerialConsoleConfig, "serial-console",
            enable=True, confirm=False, bios_attr="TotallyUnknownAttr",
        )
    assert _mutating(redfish_service) == []
