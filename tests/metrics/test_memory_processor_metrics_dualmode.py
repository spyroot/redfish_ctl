"""Offline coverage for GB300 MemoryMetrics and ProcessorMetrics readers."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

GB300_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
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


def test_processor_metrics_reads_gb300_cpu_gpu_metrics(gb300_corpus_manager):
    """processor-metrics walks Processor Metrics links without writes."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(
        ApiRequestType.ProcessorMetrics,
        "processor-metrics",
    )

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "systems": 1,
        "processors": 6,
        "metrics": 6,
        "cpu_metrics": 2,
        "gpu_metrics": 4,
        "bandwidth_percent": 4,
        "core_voltage": 4,
        "pcie_error_blocks": 4,
        "nvidia_oem_metrics": 6,
    }

    rows = {row["MetricsUri"]: row for row in result.data["metrics"]}
    gpu_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
        "ProcessorMetrics"
    ]
    assert gpu_metrics["ProcessorId"] == "GPU_0"
    assert gpu_metrics["ProcessorUri"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0"
    )
    assert gpu_metrics["BandwidthPercent"] == 0.0
    assert gpu_metrics["OperatingSpeedMHz"] == 2070
    assert gpu_metrics["CoreVoltage"] == {
        "Reading": 0.9,
        "DataSourceUri": (
            "/redfish/v1/Chassis/HGX_GPU_0/Sensors/"
            "HGX_GPU_0_Voltage_0"
        ),
    }
    assert gpu_metrics["Nvidia"]["FP16ActivityPercent"] == 0.0
    assert gpu_metrics["Nvidia"]["PCIeRawRxBandwidthGbps"] == (
        0.0017908811569213867
    )

    cpu_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/CPU_0/"
        "ProcessorMetrics"
    ]
    assert cpu_metrics["ProcessorId"] == "CPU_0"
    assert cpu_metrics["BandwidthPercent"] is None
    assert cpu_metrics["Nvidia"]["PerformanceState"] == "Normal"

    paths = {request.path.lower() for request in requests}
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "processormetrics"
        in paths
    )
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()


def test_memory_metrics_reads_gb300_memory_and_summary_metrics(
    gb300_corpus_manager,
):
    """memory-metrics walks Memory Metrics and Processor MemorySummary links."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(ApiRequestType.MemoryMetrics, "memory-metrics")

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "systems": 1,
        "memory_modules": 6,
        "processor_memory_summaries": 4,
        "metrics": 10,
        "capacity_utilization": 8,
        "health_data": 2,
        "lifetime": 8,
        "nvidia_oem_metrics": 6,
    }

    rows = {row["MetricsUri"]: row for row in result.data["metrics"]}
    memory_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Memory/GPU_0_DRAM_0/"
        "MemoryMetrics"
    ]
    assert memory_metrics["ParentType"] == "Memory"
    assert memory_metrics["ParentId"] == "GPU_0_DRAM_0"
    assert memory_metrics["BandwidthPercent"] == 0.0
    assert memory_metrics["CapacityUtilizationPercent"] == 0
    assert memory_metrics["OperatingSpeedMHz"] == 3996
    assert memory_metrics["LifeTime"] == {
        "CorrectableECCErrorCount": 0,
        "UncorrectableECCErrorCount": 0,
    }
    assert memory_metrics["Nvidia"]["RowRemapping"] == {
        "CorrectableRowRemappingCount": 0,
        "HighAvailabilityBankCount": 0,
        "LowAvailabilityBankCount": 0,
        "MaxAvailabilityBankCount": 5952,
        "NoAvailabilityBankCount": 0,
        "PartialAvailabilityBankCount": 0,
        "UncorrectableRowRemappingCount": 0,
    }

    summary_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
        "MemorySummary/MemoryMetrics"
    ]
    assert summary_metrics["ParentType"] == "ProcessorMemorySummary"
    assert summary_metrics["ParentId"] == "GPU_0"

    paths = {request.path.lower() for request in requests}
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/memory/gpu_0_dram_0/"
        "memorymetrics"
        in paths
    )
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "memorysummary/memorymetrics"
        in paths
    )
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
