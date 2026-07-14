"""Concurrent-read coverage for fleet and proxy paths."""

from __future__ import annotations

import importlib.util
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.fleet.cmd_fleet import FleetNode, read_fleet
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.proxy import NodeConfig, NodeRegistry, ReadOnlyProxy

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
GB300_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}
SYSTEM_PATH = "/redfish/v1/systems/system_0"
FLEET_READ_MAX_GETS_PER_NODE = 456
PROXY_STATUS_MAX_GETS_PER_NODE = 120


def _fixture_for_path(path: str) -> Path | None:
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


class HostOverlayResponder:
    """Serve the GB300 corpus with per-host System_0 status overrides."""

    def __init__(self, system_status_by_host: dict[str, tuple[str, str]]):
        self.system_status_by_host = system_status_by_host
        self.requests: list[tuple[str, str, str]] = []
        self.counts: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def get_cb(self, request, context) -> str:
        parsed = urlsplit(request.url)
        host = parsed.hostname or ""
        method = request.method
        path = request.path.rstrip("/")
        with self._lock:
            self.requests.append((host, method, path))
            self.counts[(host, method)] += 1

        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})

        data = json.loads(fixture.read_text(encoding="utf-8"))
        override = self.system_status_by_host.get(host)
        if path.lower() == SYSTEM_PATH and override is not None:
            power_state, health = override
            data["PowerState"] = power_state
            status = data.setdefault("Status", {})
            if isinstance(status, dict):
                status["Health"] = health
        context.status_code = 200
        return json.dumps(data)

    def get_count(self, host: str) -> int:
        return self.counts[(host, "GET")]

    def methods(self) -> set[str]:
        return {method for _, method, _ in self.requests}


def _fleet_node(name: str, address: str) -> FleetNode:
    return FleetNode(
        name=name,
        address=address,
        username="root",
        password="mock",
        port=443,
        insecure=True,
        use_http=False,
    )


def _node_config(node_id: str, address: str, *, port: int = 443) -> NodeConfig:
    return NodeConfig(
        id=node_id,
        address=address,
        port=port,
        username="root",
        password="mock",
        insecure=True,
    )


def _manager_for_node(node: NodeConfig, *, use_http: bool = False) -> RedfishManagerBase:
    return RedfishManagerBase(
        idrac_ip=node.address,
        idrac_username=node.username or "root",
        idrac_password=node.password or "mock",
        idrac_port=node.port,
        insecure=node.insecure,
        is_http=use_http,
    )


def _load_server_module():
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_read_fleet_concurrent_nodes_keep_results_isolated_and_bounded() -> None:
    """Fleet fan-out keeps each BMC's status isolated under concurrency."""
    requests_mock = pytest.importorskip("requests_mock")
    nodes = (
        _fleet_node("gb300-a", "node-a.example.test"),
        _fleet_node("gb300-b", "node-b.example.test"),
        _fleet_node("gb300-c", "node-c.example.test"),
    )
    responder = HostOverlayResponder({
        "node-a.example.test": ("On", "OK"),
        "node-b.example.test": ("Off", "Warning"),
        "node-c.example.test": ("PoweringOn", "Critical"),
    })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=responder.get_cb)
        result = read_fleet(nodes, concurrency=3)

    assert result["summary"] == {"total": 3, "ok": 3, "failed": 0}
    rows = {row["name"]: row for row in result["nodes"]}
    assert rows["gb300-a"]["powerState"] == "On"
    assert rows["gb300-a"]["health"] == "OK"
    assert rows["gb300-b"]["powerState"] == "Off"
    assert rows["gb300-b"]["health"] == "Warning"
    assert rows["gb300-c"]["powerState"] == "PoweringOn"
    assert rows["gb300-c"]["health"] == "Critical"
    assert all(row["sensors"]["count"] == 266 for row in rows.values())
    assert all(row["temperature"]["count"] == 72 for row in rows.values())
    assert responder.methods() == {"GET"}
    for node in nodes:
        assert responder.get_count(node.address) <= FLEET_READ_MAX_GETS_PER_NODE


def test_proxy_concurrent_node_status_keeps_node_identity_and_budget() -> None:
    """Proxy status reads keep node identity and request counts under fan-out."""
    requests_mock = pytest.importorskip("requests_mock")
    nodes = [
        _node_config("node-a", "node-a.example.test"),
        _node_config("node-b", "node-b.example.test"),
    ]
    responder = HostOverlayResponder({
        "node-a.example.test": ("On", "OK"),
        "node-b.example.test": ("Off", "Warning"),
    })
    proxy = ReadOnlyProxy(
        NodeRegistry(nodes),
        manager_factory=lambda node: _manager_for_node(node),
        clock=lambda: datetime(2026, 7, 12, 19, 0, tzinfo=timezone.utc),
    )

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=responder.get_cb)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(proxy.node_status, ["node-a", "node-b"]))

    by_id = {status["id"]: status for status in statuses}
    assert by_id["node-a"]["system"]["powerState"] == "On"
    assert by_id["node-a"]["system"]["health"] == "OK"
    assert by_id["node-b"]["system"]["powerState"] == "Off"
    assert by_id["node-b"]["system"]["health"] == "Warning"
    assert all(status["temperature"]["count"] == 56 for status in statuses)
    assert {status["lastPolled"] for status in statuses} == {
        "2026-07-12T19:00:00Z"
    }
    assert responder.methods() == {"GET"}
    for node in nodes:
        assert responder.get_count(node.address) <= PROXY_STATUS_MAX_GETS_PER_NODE


def test_proxy_same_node_concurrent_status_reads_live_mock_are_complete() -> None:
    """Same-node proxy readers can share one live mock BMC without partial data."""
    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, GB300_CORPUS) as server:
        host, port = server.server_address
        node = _node_config("gb300-live", host, port=port)
        proxy = ReadOnlyProxy(
            NodeRegistry([node]),
            manager_factory=lambda cfg: _manager_for_node(cfg, use_http=True),
            clock=lambda: datetime(2026, 7, 12, 19, 0, tzinfo=timezone.utc),
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(
                lambda _: proxy.node_status("gb300-live"),
                range(16),
            ))

    assert len(statuses) == 16
    assert {status["id"] for status in statuses} == {"gb300-live"}
    assert {status["system"]["id"] for status in statuses} == {"System_0"}
    assert {status["system"]["powerState"] for status in statuses} == {"On"}
    assert {status["system"]["health"] for status in statuses} == {"OK"}
    assert all(status["temperature"]["count"] == 56 for status in statuses)
    assert all(status["temperature"]["maxCelsius"] > 50 for status in statuses)
