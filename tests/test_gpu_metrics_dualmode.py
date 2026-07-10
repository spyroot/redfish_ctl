"""Offline coverage for the GB300 GPU metrics reader."""

import json
from pathlib import Path

import pytest

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

GB300_CORPUS = (
    Path(__file__).parent
    / "supermicro_gb300_corpus"
    / "json_responses"
    / "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


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


def test_gpu_metrics_reads_gb300_gpu_temperature_compute_and_memory(
    gb300_corpus_manager,
):
    """gpu-metrics aggregates GPU Processor, Sensor, and MemoryMetrics links."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(ApiRequestType.GpuMetrics, "gpu-metrics")

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "systems": 1,
        "processors": 6,
        "gpus": 4,
        "temperature_sensors": 12,
        "memory_metrics": 4,
        "summary_memory_metrics": 4,
        "compute_fields": 60,
        "throttle_fields": 16,
    }

    rows = {row["GpuId"]: row for row in result.data["gpus"]}
    assert set(rows) == {"GPU_0", "GPU_1", "GPU_2", "GPU_3"}

    gpu0 = rows["GPU_0"]
    assert gpu0["SystemId"] == "HGX_Baseboard_0"
    assert gpu0["ProcessorUri"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0"
    )
    assert gpu0["ProcessorMetricsUri"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
        "ProcessorMetrics"
    )
    assert gpu0["Model"] == "NVIDIA GB300"
    assert gpu0["Status"] == {
        "Health": "OK",
        "HealthRollup": "OK",
        "State": "Enabled",
    }
    assert gpu0["OperatingSpeedMHz"] == 2070
    assert gpu0["ProcessorMetrics"]["OperatingSpeedMHz"] == 2070
    assert gpu0["TemperaturesCelsius"] == {
        "HGX_GPU_0_DRAM_0_Temp_0": 32.12890625,
        "HGX_GPU_0_TEMP_0": 32.9375,
        "HGX_GPU_0_TEMP_1": 54.0625,
    }
    assert gpu0["ComputeUtilizationPercent"]["fp32_activity"] == 0.0
    assert gpu0["ComputeUtilizationPercent"]["sm"] == 0.0
    assert gpu0["ComputeUtilizationPercent"]["tensor_core_activity"] == 0.0
    assert gpu0["ThrottleDurationSeconds"] == {
        "global_software_violation": 603411.224568128,
        "hardware_violation": 0.0,
        "power_limit": 0.0,
        "thermal_limit": 0.0,
    }
    assert gpu0["Memory"][0]["MemoryId"] == "GPU_0_DRAM_0"
    assert gpu0["Memory"][0]["MemoryType"] == "DRAM"
    assert gpu0["Memory"][0]["CapacityUtilizationPercent"] == 0
    assert gpu0["Memory"][0]["OperatingSpeedMHz"] == 3996
    assert gpu0["Memory"][0]["EccErrors"] == {
        "correctable": 0,
        "uncorrectable": 0,
    }
    assert gpu0["Memory"][0]["RowRemapping"]["max_availability"] == 5952
    assert gpu0["Memory"][0]["RowRemappingFailed"] is False
    assert gpu0["MemorySummaryMetrics"]["CapacityUtilizationPercent"] == 0
    assert gpu0["MemorySummaryMetrics"]["EccErrors"] == {
        "correctable": 0,
        "uncorrectable": 0,
    }

    paths = {request.path.lower() for request in requests}
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "processormetrics"
        in paths
    )
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/memory/gpu_0_dram_0/"
        "memorymetrics"
        in paths
    )
    assert "/redfish/v1/chassis/hgx_gpu_0/sensors/hgx_gpu_0_temp_0" in paths
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
