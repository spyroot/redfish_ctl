"""Tests for the small typed API facade used by controller code."""

import json
from pathlib import Path

import pytest

from redfish_ctl.api import (
    FanReading,
    GpuMetricRow,
    GpuMetricsStatus,
    NtpApplied,
    NtpSetResult,
    NtpSkipped,
    NtpTarget,
    SensorReading,
    SystemStatus,
    TemperatureReading,
    ThermalStatus,
    get_gpu_metrics,
    get_sensors,
    get_system,
    get_thermal,
    set_ntp,
)
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

GB300_CORPUS = (
    Path(__file__).parent
    / "supermicro_gb300_corpus"
    / "json_responses"
    / "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


class RecordingManager:
    """Record sync_invoke calls and return configured command payloads."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def sync_invoke(self, api_call, name, **kwargs):
        self.calls.append((api_call, name, kwargs))
        return self.results[(api_call, name)]


def _fixture_for_path(path):
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


@pytest.fixture
def gb300_corpus_manager():
    """Serve the committed GB300 crawl over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = IDracManager(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def test_get_system_returns_typed_status_from_system_query():
    """get_system delegates to system_query and exposes controller fields."""
    manager = RecordingManager({
        (ApiRequestType.SystemQuery, "system_query"): CommandResult(
            {
                "Id": "System.Embedded.1",
                "Name": "Primary system",
                "PowerState": "On",
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            None,
            None,
            None,
        )
    })

    status = get_system(manager, deep=True)

    assert status == SystemStatus(
        id="System.Embedded.1",
        name="Primary system",
        power_state="On",
        health="OK",
        state="Enabled",
        raw={
            "Id": "System.Embedded.1",
            "Name": "Primary system",
            "PowerState": "On",
            "Status": {"Health": "OK", "State": "Enabled"},
        },
    )
    assert manager.calls == [
        (ApiRequestType.SystemQuery, "system_query", {"do_deep": True})
    ]


def test_get_sensors_returns_typed_readings_from_sensors_command():
    """get_sensors delegates to sensors and keeps raw sensor rows."""
    rows = [
        {
            "Chassis": "Chassis_0",
            "Name": "Front IO Temp",
            "Reading": 24.4,
            "ReadingUnits": "Cel",
            "ReadingType": "Temperature",
            "Health": "OK",
        }
    ]
    manager = RecordingManager({
        (ApiRequestType.Sensors, "sensors"): CommandResult(rows, None, None, None)
    })

    readings = get_sensors(manager, expanded=True)

    assert readings == (
        SensorReading(
            chassis="Chassis_0",
            name="Front IO Temp",
            reading=24.4,
            reading_units="Cel",
            reading_type="Temperature",
            health="OK",
            raw=rows[0],
        ),
    )
    assert manager.calls == [
        (ApiRequestType.Sensors, "sensors", {"do_expanded": True})
    ]


def test_get_thermal_returns_typed_summary_temperatures_and_fans():
    """get_thermal delegates to thermal and shapes controller-facing rows."""
    payload = {
        "summary": {
            "chassis": 1,
            "thermal_subsystems": 1,
            "thermal_metrics": 1,
            "fan_collections": 1,
            "fans": 1,
            "temperature_readings": 1,
        },
        "temperature_readings": [
            {
                "Chassis": "Chassis_0",
                "DeviceName": "Front IO Temp",
                "PhysicalContext": "Intake",
                "ReadingCelsius": 24.4,
                "DataSourceUri": "/redfish/v1/Chassis/Chassis_0/Sensors/Front",
            }
        ],
        "fans": [
            {
                "Chassis": "Chassis_0",
                "Name": "Fan 1",
                "State": "Enabled",
                "Health": "OK",
                "SpeedPercent": 44,
                "Uri": "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans/1",
            }
        ],
    }
    manager = RecordingManager({
        (ApiRequestType.Thermal, "thermal"): CommandResult(payload, None, None, None)
    })

    thermal = get_thermal(manager)

    assert thermal == ThermalStatus(
        summary=payload["summary"],
        temperatures=(
            TemperatureReading(
                chassis="Chassis_0",
                device_name="Front IO Temp",
                physical_context="Intake",
                reading_celsius=24.4,
                data_source_uri="/redfish/v1/Chassis/Chassis_0/Sensors/Front",
                raw=payload["temperature_readings"][0],
            ),
        ),
        fans=(
            FanReading(
                chassis="Chassis_0",
                name="Fan 1",
                state="Enabled",
                health="OK",
                speed_percent=44,
                uri="/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans/1",
                raw=payload["fans"][0],
            ),
        ),
        raw=payload,
    )
    assert manager.calls == [(ApiRequestType.Thermal, "thermal", {})]


def test_get_gpu_metrics_returns_typed_gpu_rows_from_command():
    payload = {
        "summary": {"systems": 1, "processors": 2, "gpus": 1},
        "gpus": [
            {
                "SystemId": "HGX_Baseboard_0",
                "GpuId": "GPU_0",
                "ProcessorUri": "/redfish/v1/Systems/HGX/Processors/GPU_0",
                "ProcessorMetricsUri": (
                    "/redfish/v1/Systems/HGX/Processors/GPU_0/"
                    "ProcessorMetrics"
                ),
                "Name": "GPU 0",
                "Model": "NVIDIA GB300",
                "Manufacturer": "NVIDIA",
                "FirmwareVersion": "97.00.00",
                "Status": {"Health": "OK", "State": "Enabled"},
                "OperatingSpeedMHz": 2070,
                "TemperaturesCelsius": {"GPU_0_TEMP": 32.9},
                "ComputeUtilizationPercent": {"fp32_activity": 0.0},
                "ThrottleDurationSeconds": {"thermal_limit": 0.0},
                "ProcessorMetrics": {"OperatingSpeedMHz": 2070},
                "Memory": [{"MemoryId": "GPU_0_DRAM_0"}],
                "MemorySummaryMetrics": {"CapacityUtilizationPercent": 0},
            }
        ],
    }
    manager = RecordingManager({
        (ApiRequestType.GpuMetrics, "gpu-metrics"): CommandResult(
            payload, None, None, None)
    })

    metrics = get_gpu_metrics(manager)

    assert metrics == GpuMetricsStatus(
        summary=payload["summary"],
        gpus=(
            GpuMetricRow(
                system_id="HGX_Baseboard_0",
                gpu_id="GPU_0",
                processor_uri="/redfish/v1/Systems/HGX/Processors/GPU_0",
                processor_metrics_uri=(
                    "/redfish/v1/Systems/HGX/Processors/GPU_0/"
                    "ProcessorMetrics"
                ),
                name="GPU 0",
                model="NVIDIA GB300",
                manufacturer="NVIDIA",
                firmware_version="97.00.00",
                status={"Health": "OK", "State": "Enabled"},
                operating_speed_mhz=2070,
                temperatures_celsius={"GPU_0_TEMP": 32.9},
                compute_utilization_percent={"fp32_activity": 0.0},
                throttle_duration_seconds={"thermal_limit": 0.0},
                processor_metrics={"OperatingSpeedMHz": 2070},
                memory=({"MemoryId": "GPU_0_DRAM_0"},),
                memory_summary_metrics={"CapacityUtilizationPercent": 0},
                raw=payload["gpus"][0],
            ),
        ),
        raw=payload,
    )
    assert manager.calls == [(ApiRequestType.GpuMetrics, "gpu-metrics", {})]


def test_set_ntp_returns_typed_dry_run_plan_by_default():
    payload = {
        "dry_run": True,
        "note": "preview only; re-run with --confirm to apply",
        "servers": ["0.pool.ntp.org", "1.pool.ntp.org"],
        "plan": [
            {
                "Manager": "BMC_0",
                "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
                "payload": {
                    "NTP": {
                        "NTPServers": ["0.pool.ntp.org", "1.pool.ntp.org"],
                        "ProtocolEnabled": True,
                    }
                },
            }
        ],
        "skipped": [
            {
                "Manager": "HGX_BMC_0",
                "target": "/redfish/v1/Managers/HGX_BMC_0/NetworkProtocol",
                "reason": "NTP block is not available",
            }
        ],
    }
    manager = RecordingManager({
        (ApiRequestType.NtpSet, "ntp-set"): CommandResult(
            payload, None, None, None)
    })

    result = set_ntp(manager, ["0.pool.ntp.org", "1.pool.ntp.org"])

    assert result == NtpSetResult(
        dry_run=True,
        servers=("0.pool.ntp.org", "1.pool.ntp.org"),
        plan=(
            NtpTarget(
                manager="BMC_0",
                target="/redfish/v1/Managers/BMC_0/NetworkProtocol",
                payload={
                    "NTP": {
                        "NTPServers": ["0.pool.ntp.org", "1.pool.ntp.org"],
                        "ProtocolEnabled": True,
                    }
                },
                raw=payload["plan"][0],
            ),
        ),
        skipped=(
            NtpSkipped(
                manager="HGX_BMC_0",
                target="/redfish/v1/Managers/HGX_BMC_0/NetworkProtocol",
                reason="NTP block is not available",
                raw=payload["skipped"][0],
            ),
        ),
        applied=(),
        note="preview only; re-run with --confirm to apply",
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            {
                "servers": ["0.pool.ntp.org", "1.pool.ntp.org"],
                "manager_id": None,
                "confirm": False,
            },
        )
    ]


def test_set_ntp_returns_typed_apply_result_when_confirmed():
    payload = {
        "servers": ["0.pool.ntp.org"],
        "applied": [
            {
                "Manager": "BMC_0",
                "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
                "status": "IdracApiRespond.Ok",
                "error": None,
            }
        ],
        "skipped": [],
    }
    manager = RecordingManager({
        (ApiRequestType.NtpSet, "ntp-set"): CommandResult(
            payload, None, None, None)
    })

    result = set_ntp(
        manager,
        ("0.pool.ntp.org",),
        manager_id="BMC_0",
        confirm=True,
    )

    assert result == NtpSetResult(
        dry_run=False,
        servers=("0.pool.ntp.org",),
        plan=(),
        skipped=(),
        applied=(
            NtpApplied(
                manager="BMC_0",
                target="/redfish/v1/Managers/BMC_0/NetworkProtocol",
                status="IdracApiRespond.Ok",
                error=None,
                raw=payload["applied"][0],
            ),
        ),
        note=None,
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            {
                "servers": ["0.pool.ntp.org"],
                "manager_id": "BMC_0",
                "confirm": True,
            },
        )
    ]


def test_facade_wrappers_read_gb300_corpus_through_command_registry(
    gb300_corpus_manager,
):
    """Facade helpers use real read commands against the GB300 fixture tree."""
    manager, requests = gb300_corpus_manager

    system = get_system(manager)
    sensors = get_sensors(manager)
    thermal = get_thermal(manager)
    gpu_metrics = get_gpu_metrics(manager)
    ntp = set_ntp(manager, ["0.pool.ntp.org"])

    assert system.id == "System_0"
    assert system.name == "System_0"
    assert system.power_state == "On"
    assert system.health == "OK"
    assert len(sensors) >= 250
    assert any(
        reading.name == "Chassis 0 Front IO Temp 0"
        and reading.reading_units == "Cel"
        for reading in sensors
    )
    assert thermal.summary["thermal_subsystems"] == 28
    assert thermal.summary["temperature_readings"] == 72
    assert any(
        reading.device_name == "Chassis_0_Front_IO_Temp_0"
        and reading.reading_celsius == 24.437
        for reading in thermal.temperatures
    )
    assert gpu_metrics.summary["gpus"] == 4
    assert any(
        row.gpu_id == "GPU_0"
        and row.model == "NVIDIA GB300"
        and row.temperatures_celsius["HGX_GPU_0_TEMP_0"] == 32.9375
        for row in gpu_metrics.gpus
    )
    assert ntp.dry_run is True
    assert ntp.servers == ("0.pool.ntp.org",)
    assert ntp.plan == (
        NtpTarget(
            manager="BMC_0",
            target="/redfish/v1/Managers/BMC_0/NetworkProtocol",
            payload={
                "NTP": {
                    "NTPServers": ["0.pool.ntp.org"],
                    "ProtocolEnabled": True,
                }
            },
            raw=ntp.raw["plan"][0],
        ),
    )

    paths = {request.path.lower() for request in requests}
    assert "/redfish/v1/systems/system_0" in paths
    assert "/redfish/v1/chassis/chassis_0/sensors" in paths
    assert "/redfish/v1/chassis/chassis_0/thermalsubsystem" in paths
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "processormetrics"
    ) in paths
    assert "/redfish/v1/managers/bmc_0/networkprotocol" in paths
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
