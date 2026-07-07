"""Dual-mode test for TelemetryService trigger discovery."""

import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_telemetry_triggers_returns_hpe_threshold_rows_without_mutation(
    redfish_mock_factory,
):
    """telemetry-triggers reads HPE Trigger rows and never mutates the BMC."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(ApiRequestType.Triggers, "telemetry-triggers")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data, sort_keys=True)
    assert len(result.data) == 4
    rows_by_id = {row["Id"]: row for row in result.data}
    assert set(rows_by_id) == {
        "CPUUtilTriggers",
        "MemoryBusUtilTriggers",
        "IOBusUtilTriggers",
        "CPUICUtilTriggers",
    }
    assert all(row["MetricType"] == "Numeric" for row in result.data)
    assert all(row["MetricProperties"] == 1 for row in result.data)
    assert all(row["TriggerActions"] == ["LogToLogService"] for row in result.data)
    assert set(rows_by_id["CPUICUtilTriggers"]["NumericThresholds"]) == {
        "UpperCritical",
    }
    assert set(rows_by_id["CPUUtilTriggers"]["NumericThresholds"]) == {
        "LowerCritical",
        "UpperCritical",
    }
    assert all(
        recorded.method not in {"POST", "PATCH", "DELETE"}
        for recorded in service.requests
    )
