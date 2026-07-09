"""Offline test for the generic, vendor-neutral `sensors` command.

Runs against the Supermicro GB300 fixtures (Chassis BMC_0 -> Sensors) through
the real requests path, proving the command navigates by links and returns
readings on a non-Dell host.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_sensors_reads_chassis_sensors(redfish_mock_factory):
    """sensors walks Chassis -> Sensors and returns readings with units."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.Sensors, "sensors")
    assert isinstance(result.data, list) and result.data, "no sensor readings"
    s = result.data[0]
    assert s["Reading"] is not None
    assert s["ReadingUnits"]
    assert "Temp" in (s["Name"] or "")
