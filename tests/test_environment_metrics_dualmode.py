"""Offline coverage for EnvironmentMetrics link discovery."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

GB300_CORPUS = corpus_dir(
    Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
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


def test_environment_metrics_reads_gb300_power_energy_and_temperature(
    gb300_corpus_manager,
):
    """environment-metrics walks linked GB300 EnvironmentMetrics resources."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(
        ApiRequestType.EnvironmentMetrics,
        "environment-metrics",
    )

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "resources": 40,
        "chassis_resources": 28,
        "processor_resources": 6,
        "memory_resources": 6,
        "power_watts": 15,
        "energy_joules": 12,
        "energy_kwh": 12,
        "temperature_celsius": 10,
        "power_limits": 11,
    }

    rows = {row["MetricsUri"]: row for row in result.data["metrics"]}
    gpu_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
        "EnvironmentMetrics"
    ]
    assert gpu_metrics["ParentType"] == "Processor"
    assert gpu_metrics["ParentId"] == "GPU_0"
    assert gpu_metrics["PowerWatts"] == 231.939
    assert gpu_metrics["EnergyJoules"] == 230235737.738
    assert gpu_metrics["EnergykWh"] == 63.95437164505238
    assert gpu_metrics["TemperatureCelsius"] == 54.0625
    assert gpu_metrics["PowerLimitWatts"] == {
        "Reading": 231.939,
        "SetPoint": 1400,
        "DefaultSetPoint": 1400,
        "AllowableMin": 200,
        "AllowableMax": 1400,
        "ControlMode": "Automatic",
    }

    memory_metrics = rows[
        "/redfish/v1/Systems/HGX_Baseboard_0/Memory/GPU_0_DRAM_0/"
        "EnvironmentMetrics"
    ]
    assert memory_metrics["ParentType"] == "Memory"
    assert memory_metrics["ParentId"] == "GPU_0_DRAM_0"
    assert memory_metrics["PowerWatts"] == 34.458
    assert memory_metrics["TemperatureCelsius"] == 32.12890625

    chassis_metrics = rows[
        "/redfish/v1/Chassis/HGX_ProcessorModule_0/EnvironmentMetrics"
    ]
    assert chassis_metrics["ParentType"] == "Chassis"
    assert chassis_metrics["ParentId"] == "HGX_ProcessorModule_0"
    assert chassis_metrics["PowerLimitWatts"] == {
        "Reading": None,
        "SetPoint": 2900,
        "DefaultSetPoint": 2900,
        "AllowableMin": 500,
        "AllowableMax": 2900,
        "ControlMode": "Automatic",
    }

    paths = {request.path.lower() for request in requests}
    assert "/redfish/v1/chassis/hgx_gpu_0/environmentmetrics" in paths
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "environmentmetrics"
        in paths
    )
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/memory/gpu_0_dram_0/"
        "environmentmetrics"
        in paths
    )
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
