"""Dual-mode tests for the serial-console command (report + guarded enable).

Read/status is non-mutating; --enable previews unless --confirm is given. These run
offline against the mock and assert that neither path sends a PATCH without --confirm.
"""
from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult
from idrac_ctl.serial_console.cmd_serial_console import (  # noqa: F401
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


def test_serial_console_enable_needs_value_for_unknown_attr(redfish_mock, redfish_service):
    """An unknown BIOS attribute with no --bios_value is rejected before any write."""
    import pytest

    with pytest.raises(Exception):
        redfish_mock.sync_invoke(
            ApiRequestType.SerialConsoleConfig, "serial-console",
            enable=True, confirm=False, bios_attr="TotallyUnknownAttr",
        )
    assert _mutating(redfish_service) == []
