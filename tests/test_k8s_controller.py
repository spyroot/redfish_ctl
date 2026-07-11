"""Contracts for the read-only Kubernetes RedfishEndpoint controller."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from redfish_ctl.api import (
    FanReading,
    SensorReading,
    SystemStatus,
    TemperatureReading,
    ThermalStatus,
)
from redfish_ctl.idrac_manager import IDracManager

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_MODULE = REPO_ROOT / "k8s" / "controller" / "redfish_endpoint_controller.py"
CRD_MANIFEST = REPO_ROOT / "k8s" / "controller" / "redfish-endpoint-crd.yaml"
GB300_CORPUS = (
    REPO_ROOT
    / "tests"
    / "supermicro_gb300_corpus"
    / "json_responses"
    / "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


def _load_controller_module():
    spec = importlib.util.spec_from_file_location(
        "redfish_endpoint_controller",
        CONTROLLER_MODULE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_for_path(path: str) -> Path | None:
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


def test_crd_schema_pins_read_only_endpoint_spec_and_status_shape() -> None:
    """The CRD exposes only connection fields and read-only status."""
    crd = yaml.safe_load(CRD_MANIFEST.read_text(encoding="utf-8"))
    version = crd["spec"]["versions"][0]
    schema = version["schema"]["openAPIV3Schema"]
    spec_props = schema["properties"]["spec"]["properties"]
    status_props = schema["properties"]["status"]["properties"]

    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["spec"]["names"]["kind"] == "RedfishEndpoint"
    assert crd["spec"]["scope"] == "Namespaced"
    assert set(spec_props) == {
        "address",
        "port",
        "insecure",
        "pollInterval",
        "secretRef",
    }
    secret_ref_props = spec_props["secretRef"]["properties"]
    assert secret_ref_props["name"]["type"] == "string"
    assert secret_ref_props["usernameKey"]["default"] == "username"
    assert secret_ref_props["passwordKey"]["default"] == "password"
    assert set(status_props) == {
        "powerState",
        "health",
        "temperature",
        "lastPolled",
    }
    assert status_props["temperature"]["properties"]["maxCelsius"]["type"] == "number"
    assert "valueFrom" not in json.dumps(crd)
    assert version["additionalPrinterColumns"] == [
        {
            "name": "POWER",
            "type": "string",
            "jsonPath": ".status.powerState",
        },
        {
            "name": "HEALTH",
            "type": "string",
            "jsonPath": ".status.health",
        },
        {
            "name": "POLLED",
            "type": "date",
            "jsonPath": ".status.lastPolled",
        },
    ]


def test_build_status_tolerates_missing_values_and_summarizes_temperatures() -> None:
    """Status rendering keeps fields optional and ignores non-numeric temperatures."""
    module = _load_controller_module()
    polled_at = datetime(2026, 7, 10, 14, 40, 0, tzinfo=timezone.utc)
    system = SystemStatus(
        id="System_0",
        name="System_0",
        power_state="On",
        health="OK",
        state="Enabled",
        raw={},
    )
    thermal = ThermalStatus(
        summary={},
        temperatures=(
            TemperatureReading("Chassis_0", "Inlet", "Intake", 24.4, "/sensor/1", {}),
            TemperatureReading("Chassis_0", "Outlet", "Exhaust", "31.2", "/sensor/2", {}),
            TemperatureReading("Chassis_0", "Bad", "Unknown", None, "/sensor/3", {}),
        ),
        fans=(
            FanReading("Chassis_0", "Fan 1", "Enabled", "OK", 42, "/fan/1", {}),
        ),
        raw={},
    )

    status = module.build_status(system, (), thermal, polled_at=polled_at)

    assert status == {
        "powerState": "On",
        "health": "OK",
        "temperature": {
            "count": 2,
            "maxCelsius": 31.2,
        },
        "lastPolled": "2026-07-10T14:40:00Z",
    }


def test_poll_endpoint_reads_gb300_corpus_without_mutating_requests() -> None:
    """The poll path uses read commands through the facade and never writes."""
    requests_mock = pytest.importorskip("requests_mock")
    module = _load_controller_module()
    seen_methods: list[str] = []

    def get_cb(request, context):
        seen_methods.append(request.method)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text(encoding="utf-8")

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = IDracManager(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            idrac_port=8080,
            insecure=True,
            is_http=True,
            is_debug=False,
        )
        status = module.poll_endpoint(
            {
                "address": "mock-gb300",
                "port": 8080,
                "insecure": True,
                "pollInterval": "30s",
                "secretRef": {"name": "bmc-login"},
            },
            credentials={"username": "root", "password": "mock"},
            manager_factory=lambda **_: manager,
            polled_at=datetime(2026, 7, 10, 14, 45, 0, tzinfo=timezone.utc),
        )

    assert status["powerState"] == "On"
    assert status["health"] == "OK"
    assert status["temperature"]["count"] == 56
    assert status["temperature"]["maxCelsius"] == 54.1875
    assert status["lastPolled"] == "2026-07-10T14:45:00Z"
    assert seen_methods
    assert set(seen_methods) == {"GET"}


def test_kopf_handler_patches_status_only(monkeypatch) -> None:
    """The handler writes status through the kopf patch, returns None, never mutates."""
    module = _load_controller_module()
    calls: list[tuple[dict, dict]] = []

    def fake_poll_endpoint(spec, credentials):
        calls.append((spec, credentials))
        return {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 1, "maxCelsius": 24.4},
            "lastPolled": "2026-07-10T14:50:00Z",
        }

    monkeypatch.setattr(module, "poll_endpoint", fake_poll_endpoint)

    patch: dict = {}
    result = module.poll_redfish_endpoint(
        spec={"address": "mock-bmc", "secretRef": {"name": "bmc-login"}},
        body={},
        namespace="default",
        name="node-a",
        logger=None,
        patch=patch,
    )

    # Status is applied via the injected patch; the handler returns None so kopf
    # does not persist a result under a status field the structural CRD rejects
    # (the source of the "merge-patching inconsistencies" warning every poll).
    assert result is None
    assert patch == {
        "status": {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 1, "maxCelsius": 24.4},
            "lastPolled": "2026-07-10T14:50:00Z",
        }
    }
    assert calls == [
        (
            {"address": "mock-bmc", "secretRef": {"name": "bmc-login"}},
            {},
        )
    ]


def test_sensor_health_falls_back_when_system_health_absent() -> None:
    """Sensor health gives the status a useful fallback when system health is missing."""
    module = _load_controller_module()
    system = SystemStatus(None, None, "Off", None, None, {})
    sensors = (
        SensorReading("Chassis_0", "Temp", 20, "Cel", "Temperature", "OK", {}),
        SensorReading("Chassis_0", "Fan", 40, "%", "Fan", "Warning", {}),
    )
    thermal = ThermalStatus({}, (), (), {})

    status = module.build_status(
        system,
        sensors,
        thermal,
        polled_at=datetime(2026, 7, 10, 14, 55, tzinfo=timezone.utc),
    )

    assert status["powerState"] == "Off"
    assert status["health"] == "Warning"
    assert status["temperature"] == {"count": 0, "maxCelsius": None}
