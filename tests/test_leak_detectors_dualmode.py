"""Offline coverage for the GB300 LeakDetection reader."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType

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
        manager = CommandBase(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def test_leak_detectors_read_gb300_detection_state_and_policy(
    gb300_corpus_manager,
):
    """leak-detectors walks GB300 LeakDetection resources without writes."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(
        ApiRequestType.LeakDetectors,
        "leak-detectors",
    )

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "chassis": 42,
        "leak_detection_subsystems": 1,
        "detector_collections": 1,
        "detectors": 4,
        "detectors_ok": 4,
        "detectors_warning": 0,
        "detectors_critical": 0,
        "policies": 1,
        "enabled_policies": 0,
    }

    subsystem = result.data["subsystems"][0]
    assert subsystem == {
        "Chassis": "Chassis_0",
        "Name": "Leak Detection Systems",
        "State": "Enabled",
        "Health": "OK",
        "HealthRollup": "OK",
        "Uri": "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/LeakDetection",
        "LeakDetectorsUri": (
            "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/"
            "LeakDetection/LeakDetectors"
        ),
    }

    detectors = {row["Id"]: row for row in result.data["detectors"]}
    assert detectors["Chassis_0_LeakDetector_0_ColdPlate"] == {
        "Chassis": "Chassis_0",
        "Id": "Chassis_0_LeakDetector_0_ColdPlate",
        "Name": "Chassis 0 LeakDetector 0 ColdPlate",
        "DetectorState": "OK",
        "LeakDetectorType": "Moisture",
        "State": "Enabled",
        "Health": "OK",
        "Uri": (
            "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/"
            "LeakDetection/LeakDetectors/Chassis_0_LeakDetector_0_ColdPlate"
        ),
    }

    policy = result.data["policies"][0]
    assert policy["Chassis"] == "Chassis_0"
    assert policy["Id"] == "LeakDetectionPolicy"
    assert policy["PolicyEnabled"] is False
    assert policy["PolicyConditionLogic"] == "AnyOf"
