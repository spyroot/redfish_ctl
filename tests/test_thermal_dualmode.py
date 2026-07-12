"""Offline coverage for the GB300 ThermalSubsystem reader."""

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


def test_thermal_reads_gb300_subsystems_metrics_and_fan_collections(
    gb300_corpus_manager,
):
    """thermal walks the GB300 Chassis ThermalSubsystem links."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(ApiRequestType.Thermal, "thermal")

    assert result.data["summary"] == {
        "chassis": 42,
        "thermal_subsystems": 28,
        "thermal_metrics": 28,
        "fan_collections": 15,
        "fans": 0,
        "temperature_readings": 72,
    }

    subsystems = {row["Chassis"]: row for row in result.data["subsystems"]}
    assert subsystems["Chassis_0"]["Health"] == "OK"
    assert subsystems["Chassis_0"]["State"] == "Enabled"
    assert subsystems["Chassis_0"]["ThermalMetricsUri"] == (
        "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    )
    assert subsystems["Chassis_0"]["FansUri"] == (
        "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans"
    )

    fan_collections = {
        row["Chassis"]: row for row in result.data["fan_collections"]
    }
    assert fan_collections["Chassis_0"] == {
        "Chassis": "Chassis_0",
        "Name": "Fan Collection",
        "MemberCount": 0,
        "Uri": "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans",
    }

    temps = {
        (row["Chassis"], row["DeviceName"]): row
        for row in result.data["temperature_readings"]
    }
    assert temps[("Chassis_0", "Chassis_0_Front_IO_Temp_0")] == {
        "Chassis": "Chassis_0",
        "DeviceName": "Chassis_0_Front_IO_Temp_0",
        "PhysicalContext": None,
        "ReadingCelsius": 24.437,
        "DataSourceUri": (
            "/redfish/v1/Chassis/Chassis_0/Sensors/"
            "Chassis_0_Front_IO_Temp_0"
        ),
    }

    paths = {request.path.lower() for request in requests}
    assert "/redfish/v1/chassis/chassis_0/thermalsubsystem" in paths
    assert "/redfish/v1/chassis/chassis_0/thermalsubsystem/fans" in paths
    assert (
        "/redfish/v1/chassis/chassis_0/thermalsubsystem/thermalmetrics"
        in paths
    )
