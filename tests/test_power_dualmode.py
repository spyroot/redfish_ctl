"""Offline coverage for the GB300 PowerSubsystem reader."""

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


def test_power_reads_gb300_subsystems_and_supply_collections(
    gb300_corpus_manager,
):
    """power walks GB300 Chassis PowerSubsystem links without writes."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(ApiRequestType.Power, "power")

    assert result.data["summary"] == {
        "chassis": 42,
        "power_subsystems": 28,
        "power_supply_collections": 28,
        "power_supplies": 0,
        "battery_collections": 0,
        "batteries": 0,
    }

    subsystems = {row["Chassis"]: row for row in result.data["subsystems"]}
    assert subsystems["Chassis_0"] == {
        "Chassis": "Chassis_0",
        "Name": "Power Subsystem",
        "State": "Enabled",
        "Health": "OK",
        "HealthRollup": None,
        "CapacityWatts": None,
        "AllocatedWatts": None,
        "RequestedWatts": None,
        "Uri": "/redfish/v1/Chassis/Chassis_0/PowerSubsystem",
        "PowerSuppliesUri": (
            "/redfish/v1/Chassis/Chassis_0/PowerSubsystem/PowerSupplies"
        ),
        "BatteriesUri": None,
    }

    collections = {
        row["Chassis"]: row
        for row in result.data["power_supply_collections"]
    }
    assert collections["Chassis_0"] == {
        "Chassis": "Chassis_0",
        "Name": "Power Supply Collection",
        "MemberCount": 0,
        "Uri": "/redfish/v1/Chassis/Chassis_0/PowerSubsystem/PowerSupplies",
    }

    paths = {request.path.lower() for request in requests}
    assert "/redfish/v1/chassis/chassis_0/powersubsystem" in paths
    assert (
        "/redfish/v1/chassis/chassis_0/powersubsystem/powersupplies"
        in paths
    )
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
