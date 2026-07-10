"""Tests for the read-only fleet proxy core."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.proxy import NodeConfig, NodeRegistry, ReadOnlyProxy, create_app
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


def _node():
    return NodeConfig(
        id="gb300-a",
        address="redfish://203.0.113.10",
        username="operator",
        password="do-not-expose",
        description="Rack A node",
    )


def test_proxy_lists_nodes_without_exposing_credentials():
    """Node inventory responses omit username and password fields."""
    proxy = ReadOnlyProxy(
        NodeRegistry([_node()]),
        manager_factory=lambda node: RecordingManager({}),
    )

    payload = proxy.list_nodes()

    assert payload == {
        "nodes": [
            {
                "id": "gb300-a",
                "address": "redfish://203.0.113.10",
                "port": 443,
                "insecure": True,
                "description": "Rack A node",
            }
        ]
    }
    encoded = json.dumps(payload)
    assert "operator" not in encoded
    assert "do-not-expose" not in encoded


def test_proxy_status_uses_facade_and_summarizes_temperatures():
    """Node status reads system and thermal data without mutating the BMC."""
    manager = RecordingManager({
        (ApiRequestType.SystemQuery, "system_query"): CommandResult(
            {
                "Id": "System_0",
                "Name": "System_0",
                "PowerState": "On",
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            None,
            None,
            None,
        ),
        (ApiRequestType.Thermal, "thermal"): CommandResult(
            {
                "summary": {},
                "temperature_readings": [
                    {"ReadingCelsius": 39.25},
                    {"ReadingCelsius": "42.5"},
                    {"ReadingCelsius": None},
                    {"ReadingCelsius": "not-a-number"},
                ],
                "fans": [],
            },
            None,
            None,
            None,
        ),
    })
    proxy = ReadOnlyProxy(
        NodeRegistry([_node()]),
        manager_factory=lambda node: manager,
        clock=lambda: datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc),
    )

    payload = proxy.node_status("gb300-a")

    assert payload == {
        "id": "gb300-a",
        "address": "redfish://203.0.113.10",
        "system": {
            "id": "System_0",
            "name": "System_0",
            "powerState": "On",
            "health": "OK",
            "state": "Enabled",
        },
        "temperature": {"count": 2, "maxCelsius": 42.5},
        "lastPolled": "2026-07-10T18:00:00Z",
    }
    assert manager.calls == [
        (ApiRequestType.SystemQuery, "system_query", {"do_deep": False}),
        (ApiRequestType.Thermal, "thermal", {}),
    ]


def test_proxy_read_endpoints_delegate_to_existing_commands():
    """Sensors, GPU metrics, and BIOS endpoints reuse registered read commands."""
    manager = RecordingManager({
        (ApiRequestType.Sensors, "sensors"): CommandResult(
            [
                {
                    "Chassis": "Chassis_0",
                    "Name": "Front IO Temp",
                    "Reading": 24.4,
                    "ReadingUnits": "Cel",
                    "ReadingType": "Temperature",
                    "Health": "OK",
                }
            ],
            None,
            None,
            None,
        ),
        (ApiRequestType.GpuMetrics, "gpu-metrics"): CommandResult(
            {"summary": {"gpus": 4}, "gpus": [{"GpuId": "GPU_0"}]},
            None,
            None,
            None,
        ),
        (ApiRequestType.BiosQuery, "bios_inventory"): CommandResult(
            {"Attributes": {"ProcCStates": "Disabled"}},
            None,
            None,
            None,
        ),
    })
    proxy = ReadOnlyProxy(
        NodeRegistry([_node()]),
        manager_factory=lambda node: manager,
    )

    sensors = proxy.node_sensors("gb300-a")
    gpu_metrics = proxy.node_gpu_metrics("gb300-a")
    bios = proxy.node_bios("gb300-a", attr_filter="ProcCStates")

    assert sensors["sensors"][0]["name"] == "Front IO Temp"
    assert sensors["sensors"][0]["readingUnits"] == "Cel"
    assert gpu_metrics["gpuMetrics"]["summary"]["gpus"] == 4
    assert bios["bios"]["Attributes"] == {"ProcCStates": "Disabled"}
    assert manager.calls == [
        (ApiRequestType.Sensors, "sensors", {"do_expanded": False}),
        (ApiRequestType.GpuMetrics, "gpu-metrics", {}),
        (
            ApiRequestType.BiosQuery,
            "bios_inventory",
            {
                "attr_filter": "ProcCStates",
                "attr_only": False,
                "do_deep": False,
            },
        ),
    ]


def test_create_app_registers_read_only_routes(monkeypatch):
    """The optional FastAPI adapter exposes only GET routes."""
    routes = []

    class FakeFastAPI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get(self, path):
            def decorator(func):
                routes.append(("GET", path, func))
                return func

            return decorator

    class FakeHTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        SimpleNamespace(FastAPI=FakeFastAPI, HTTPException=FakeHTTPException),
    )
    proxy = ReadOnlyProxy(
        NodeRegistry([_node()]),
        manager_factory=lambda node: RecordingManager({}),
    )

    app = create_app(proxy)

    assert app.kwargs["title"] == "redfish_ctl read-only proxy"
    assert [(method, path) for method, path, _ in routes] == [
        ("GET", "/nodes"),
        ("GET", "/nodes/{node_id}"),
        ("GET", "/nodes/{node_id}/sensors"),
        ("GET", "/nodes/{node_id}/gpu-metrics"),
        ("GET", "/nodes/{node_id}/bios"),
    ]


def test_proxy_reads_gb300_corpus_through_registered_commands(
    gb300_corpus_manager,
):
    """Proxy reads use real commands against the GB300 fixture tree."""
    manager, requests = gb300_corpus_manager
    proxy = ReadOnlyProxy(
        NodeRegistry([_node()]),
        manager_factory=lambda node: manager,
        clock=lambda: datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc),
    )

    status = proxy.node_status("gb300-a")
    sensors = proxy.node_sensors("gb300-a")
    gpu_metrics = proxy.node_gpu_metrics("gb300-a")
    bios = proxy.node_bios("gb300-a", attr_filter="EGM")

    assert status["system"]["id"] == "System_0"
    assert status["system"]["powerState"] == "On"
    assert status["system"]["health"] == "OK"
    assert status["temperature"]["count"] == 56
    assert status["temperature"]["maxCelsius"] > 50
    assert len(sensors["sensors"]) >= 250
    assert gpu_metrics["gpuMetrics"]["summary"]["gpus"] == 4
    assert bios["bios"]["EGM"] is True
    assert bios["bios"]["EGMHypervisorReservedMemory"] == 0

    paths = {request.path.lower() for request in requests}
    assert "/redfish/v1/systems/system_0" in paths
    assert "/redfish/v1/chassis/chassis_0/sensors" in paths
    assert "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0" in paths
    assert "/redfish/v1/systems/system_0/bios" in paths
    assert {request.method for request in requests} == {"GET"}
