"""Offline coverage for Redfish Chassis Controls discovery."""

import json

from redfish_ctl.idrac_shared import ApiRequestType


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def test_controls_reads_supermicro_control_collections_and_members(
        redfish_mock_factory):
    """controls walks Chassis Controls links and never writes."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(ApiRequestType.ControlsQuery, "controls")

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "chassis": 42,
        "control_collections": 28,
        "controls": 11,
        "power_controls": 7,
        "frequency_controls": 4,
    }

    collections = {
        row["Chassis"]: row
        for row in result.data["control_collections"]
    }
    assert collections["BMC_0"] == {
        "Chassis": "BMC_0",
        "Name": "Controls",
        "MemberCount": 0,
        "Uri": "/redfish/v1/Chassis/BMC_0/Controls",
    }

    controls = {
        (row["Chassis"], row["Id"]): row
        for row in result.data["controls"]
    }
    assert controls[("HGX_Chassis_0", "TotalGPU_Power_0")] == {
        "Chassis": "HGX_Chassis_0",
        "Id": "TotalGPU_Power_0",
        "Name": "System Power Control",
        "ControlType": "Power",
        "ControlMode": "Override",
        "SetPoint": 5600,
        "SetPointUnits": "W",
        "DefaultSetPoint": 5600,
        "AllowableMin": 800,
        "AllowableMax": 5600,
        "Reading": 954.6850000000001,
        "ReadingUnits": None,
        "PhysicalContext": "GPU",
        "State": "Enabled",
        "Health": "OK",
        "Uri": (
            "/redfish/v1/Chassis/HGX_Chassis_0/Controls/"
            "TotalGPU_Power_0"
        ),
    }
    assert controls[("HGX_GPU_0", "ClockLimit_0")] == {
        "Chassis": "HGX_GPU_0",
        "Id": "ClockLimit_0",
        "Name": "Control for GPU_0 ClockLimit_0",
        "ControlType": "FrequencyMHz",
        "ControlMode": "Automatic",
        "SetPoint": None,
        "SetPointUnits": "MHz",
        "DefaultSetPoint": None,
        "AllowableMin": 120,
        "AllowableMax": 2070,
        "Reading": None,
        "ReadingUnits": None,
        "PhysicalContext": "GPU",
        "State": "Enabled",
        "Health": "OK",
        "Uri": "/redfish/v1/Chassis/HGX_GPU_0/Controls/ClockLimit_0",
    }

    paths = {request.path.lower() for request in service.requests}
    assert "/redfish/v1/chassis/hgx_gpu_0/controls" in paths
    assert (
        "/redfish/v1/chassis/hgx_gpu_0/controls/clocklimit_0"
        in paths
    )
    assert "/redfish/v1/chassis/irot_bf3_1" in paths
    assert "/redfish/v1/chassis/irot_bf3_1/controls" not in paths
    assert _mutating_requests(service) == []
