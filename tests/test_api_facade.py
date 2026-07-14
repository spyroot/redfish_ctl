"""Tests for the small typed API facade used by controller code."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.api import (
    BiosProfileApplyResult,
    BiosProfileAttributeDiff,
    BiosProfileDiffResult,
    BiosProfileSpec,
    BiosProfileSummary,
    FanReading,
    GpuMetricRow,
    GpuMetricsStatus,
    NtpApplied,
    NtpSetResult,
    NtpSkipped,
    NtpTarget,
    RebootResult,
    SensorReading,
    SystemStatus,
    TemperatureReading,
    ThermalStatus,
    bios_profile_apply,
    bios_profile_diff,
    bios_profile_list,
    bios_profile_show,
    get_gpu_metrics,
    get_sensors,
    get_system,
    get_thermal,
    reboot,
    set_ntp,
)
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

GB300_CORPUS = corpus_dir(
    Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
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
        manager = RedfishManagerBase(
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


def test_reboot_previews_host_reset_by_default():
    """reboot delegates to the reset command without POSTing by default."""
    preview = {
        "dry_run": True,
        "action": "#ComputerSystem.Reset",
        "target": "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset",
        "payload": {"ResetType": "GracefulRestart"},
    }
    manager = RecordingManager({
        (ApiRequestType.ComputerSystemReset, "reboot"): CommandResult(
            preview,
            None,
            None,
            None,
        )
    })

    result = reboot(manager)

    assert result == RebootResult(
        reset_type="GracefulRestart",
        dry_run=True,
        target="/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset",
        payload={"ResetType": "GracefulRestart"},
        task_id=None,
        task_state=None,
        raw=preview,
    )
    assert manager.calls == [
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            {
                "reset_type": "GracefulRestart",
                "dry_run": True,
                "do_wait": False,
                "do_async": False,
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
                "status": "RedfishApiRespond.Ok",
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
                status="RedfishApiRespond.Ok",
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


def test_reboot_confirm_invokes_host_reset_and_maps_task_result():
    """reboot confirm exposes the reset task fields returned by the command."""
    payload = {
        "executed": True,
        "task_id": "JID_1234",
        "task_state": "Running",
    }
    manager = RecordingManager({
        (ApiRequestType.ComputerSystemReset, "reboot"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    result = reboot(
        manager,
        reset_type="ForceRestart",
        confirm=True,
        wait=True,
        async_call=True,
    )

    assert result == RebootResult(
        reset_type="ForceRestart",
        dry_run=False,
        target=None,
        payload={},
        task_id="JID_1234",
        task_state="Running",
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            {
                "reset_type": "ForceRestart",
                "dry_run": False,
                "do_wait": True,
                "do_async": True,
            },
        )
    ]


def test_bios_profile_diff_returns_typed_attribute_rows():
    """bios_profile_diff exposes the guarded diff command result."""
    payload = {
        "profile": {
            "name": "gb300-power-capped",
            "vendor": "supermicro",
            "model": "gb300",
            "risk": "medium",
        },
        "matches": False,
        "summary": {"total": 2, "matching": 1, "different": 1, "missing": 0},
        "attributes": [
            {
                "attribute": "ServerPowerControl",
                "current": "Performance",
                "desired": "Efficiency",
                "status": "different",
            },
            {
                "attribute": "ActiveCores",
                "current": "All",
                "desired": "All",
                "status": "matching",
            },
        ],
    }
    manager = RecordingManager({
        (ApiRequestType.BiosProfile, "bios-profile"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    result = bios_profile_diff(manager, "gb300-power-capped")

    assert result == BiosProfileDiffResult(
        profile=payload["profile"],
        matches=False,
        summary=payload["summary"],
        attributes=(
            BiosProfileAttributeDiff(
                attribute="ServerPowerControl",
                current="Performance",
                desired="Efficiency",
                status="different",
                raw=payload["attributes"][0],
            ),
            BiosProfileAttributeDiff(
                attribute="ActiveCores",
                current="All",
                desired="All",
                status="matching",
                raw=payload["attributes"][1],
            ),
        ),
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {
                "action": "diff",
                "profile_name": "gb300-power-capped",
            },
        )
    ]


def test_bios_profile_list_returns_typed_summary_rows():
    """bios_profile_list exposes committed catalog summaries for services."""
    payload = [
        {
            "name": "gb300-power-capped",
            "vendor": "supermicro",
            "model": "GB300",
            "description": "Smooth rack-level power draw.",
            "risk": "medium",
        },
        {
            "name": "dell-cstates-off",
            "vendor": "dell",
            "model": "PowerEdge",
            "description": "Disable processor C-states.",
            "risk": "low",
        },
    ]
    manager = RecordingManager({
        (ApiRequestType.BiosProfile, "bios-profile"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    summaries = bios_profile_list(manager)

    assert summaries == (
        BiosProfileSummary(
            name="gb300-power-capped",
            vendor="supermicro",
            model="GB300",
            description="Smooth rack-level power draw.",
            risk="medium",
            raw=payload[0],
        ),
        BiosProfileSummary(
            name="dell-cstates-off",
            vendor="dell",
            model="PowerEdge",
            description="Disable processor C-states.",
            risk="low",
            raw=payload[1],
        ),
    )
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {"action": "list"},
        )
    ]


def test_bios_profile_show_returns_typed_profile_spec():
    """bios_profile_show exposes the selected profile attributes."""
    payload = {
        "name": "gb300-power-capped",
        "vendor": "supermicro",
        "model": "GB300",
        "description": "Smooth rack-level power draw.",
        "risk": "medium",
        "attributes": {"ServerPowerControl": "Efficiency"},
    }
    manager = RecordingManager({
        (ApiRequestType.BiosProfile, "bios-profile"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    profile = bios_profile_show(manager, "gb300-power-capped")

    assert profile == BiosProfileSpec(
        name="gb300-power-capped",
        vendor="supermicro",
        model="GB300",
        description="Smooth rack-level power draw.",
        risk="medium",
        attributes={"ServerPowerControl": "Efficiency"},
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {
                "action": "show",
                "profile_name": "gb300-power-capped",
            },
        )
    ]


def test_bios_profile_apply_previews_by_default():
    """bios_profile_apply delegates to apply without confirming by default."""
    payload = {
        "profile": "gb300-power-capped",
        "dry_run": True,
        "change": {"Attributes": {"ServerPowerControl": "Efficiency"}},
        "rollback": {"Attributes": {"ServerPowerControl": "Performance"}},
        "staged": {
            "Attributes": {"ServerPowerControl": "Efficiency"},
            "preview": True,
        },
    }
    manager = RecordingManager({
        (ApiRequestType.BiosProfile, "bios-profile"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    result = bios_profile_apply(manager, "gb300-power-capped")

    assert result == BiosProfileApplyResult(
        profile="gb300-power-capped",
        dry_run=True,
        change=payload["change"],
        rollback=payload["rollback"],
        staged=payload["staged"],
        raw=payload,
    )
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {
                "action": "apply",
                "profile_name": "gb300-power-capped",
                "confirm": False,
                "dry_run": False,
            },
        )
    ]


def test_bios_profile_apply_confirms_when_requested():
    """bios_profile_apply forwards explicit confirmation to the guarded command."""
    payload = {
        "profile": "dell-cstates-off",
        "dry_run": False,
        "change": {"Attributes": {"ProcCStates": "Disabled"}},
        "rollback": {"Attributes": {"ProcCStates": "Enabled"}},
        "staged": {
            "Attributes": {"ProcCStates": "Disabled"},
            "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
        },
    }
    manager = RecordingManager({
        (ApiRequestType.BiosProfile, "bios-profile"): CommandResult(
            payload,
            None,
            None,
            None,
        )
    })

    result = bios_profile_apply(manager, "dell-cstates-off", confirm=True)

    assert result == BiosProfileApplyResult(
        profile="dell-cstates-off",
        dry_run=False,
        change=payload["change"],
        rollback=payload["rollback"],
        staged=payload["staged"],
        raw=payload,
    )
    assert result.staged == {
        "Attributes": {"ProcCStates": "Disabled"},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {
                "action": "apply",
                "profile_name": "dell-cstates-off",
                "confirm": True,
                "dry_run": False,
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
    reset_preview = reboot(manager)
    profile_diff = bios_profile_diff(manager, "gb300-power-capped")

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
    assert reset_preview.dry_run is True
    assert reset_preview.target == (
        "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset"
    )
    assert reset_preview.payload == {"ResetType": "GracefulRestart"}
    assert profile_diff.profile["name"] == "gb300-power-capped"
    assert profile_diff.summary["total"] == 1
    assert profile_diff.attributes

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
