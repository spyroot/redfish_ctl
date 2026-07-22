"""Dual-mode tests for the generic sensors command."""

import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _assert_dell_sensor_reading(result):
    """Assert the normalized Dell sensor reading returned by sensors."""
    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data)
    assert result.data == [
        {
            "Chassis": "System.Embedded.1",
            "Name": "System Board Inlet Temp",
            "Reading": 23,
            "ReadingUnits": "Cel",
            "ReadingType": "Temperature",
            "Health": "OK",
        }
    ]


def test_sensors_returns_dell_chassis_sensor_reading(redfish_api):
    """sensors walks Dell Chassis -> Sensors links in dual-mode."""
    result = redfish_api.sync_invoke(ApiRequestType.Sensors, "sensors")

    _assert_dell_sensor_reading(result)


def test_sensors_fetches_linked_sensor_resource_in_mock_mode(
    redfish_mock,
    redfish_service,
):
    """sensors fetches each linked Sensor resource through the mock transport."""
    result = redfish_mock.sync_invoke(ApiRequestType.Sensors, "sensors")

    _assert_dell_sensor_reading(result)

    sensor_gets = [
        request
        for request in redfish_service.requests
        if request.method == "GET"
        and request.path.lower()
        == "/redfish/v1/chassis/system.embedded.1/sensors/inlettemp"
    ]
    assert len(sensor_gets) == 1
