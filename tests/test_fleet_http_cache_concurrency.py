"""Fleet-consumer HTTP server cache behaviour under many concurrent clients.

The consumer's ``_Handler`` fronts the Kubernetes API with a short-TTL cache so a
crowd of dashboard clients does not fan out into one API call per hit. This test
drives a synchronized burst of ``/``, ``/api/nodes`` and ``/metrics`` requests at
the live HTTP server (with a fake ``load_endpoints``) and asserts the whole burst
shares a single load within the TTL, then reloads exactly once after the TTL —
proving the single-flight cache holds end-to-end, not just in the unit.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FLEET_MODULE = REPO_ROOT / "k8s" / "consumer" / "fleet_status_app.py"
NODE_COUNT = 50


def _load_fleet_module() -> Any:
    spec = importlib.util.spec_from_file_location("fleet_status_app", FLEET_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fake_nodes(module: Any) -> list[dict[str, Any]]:
    rows = []
    for i in range(NODE_COUNT):
        obj = {
            "metadata": {"name": f"node-{i:02d}", "namespace": "test-ns"},
            "spec": {"address": f"https://mock-{i}.svc.cluster.local", "port": 443},
            "status": {
                "powerState": "On",
                "health": "OK",
                "lastPolled": "2026-07-15T00:00:00Z",
                "temperature": {"count": 8, "maxCelsius": 60.0},
            },
        }
        rows.append(module.normalize_endpoint(obj))
    return rows


def _get(base: str, path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return response.status, response.read()


def test_fleet_http_cache_single_flight_and_ttl_reload() -> None:
    """A concurrent client burst triggers one load per TTL, never a herd."""
    module = _load_fleet_module()

    loads = 0
    loads_lock = threading.Lock()
    rows = _fake_nodes(module)

    def fake_load(_namespace: str) -> list[dict[str, Any]]:
        nonlocal loads
        with loads_lock:
            loads += 1
        time.sleep(0.05)  # emulate k8s API latency so a herd would overlap
        return list(rows)

    module.load_endpoints = fake_load  # handler resolves this at call time

    class Handler(module._Handler):
        pass

    Handler.namespace = "test-ns"
    # Generous TTL: the whole burst must fall inside one TTL window, so a straggler
    # thread delayed by scheduling can't legitimately expire it and load twice.
    # Reload-after-expiry is proven deterministically below (and in the unit test).
    Handler.cache = module._EndpointCache(ttl_seconds=30.0)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://{}:{}".format(*server.server_address)
        paths = ["/", "/api/nodes", "/metrics"]
        clients = 60
        barrier = threading.Barrier(clients)

        def worker(i: int) -> tuple[int, bytes, str]:
            path = paths[i % len(paths)]
            barrier.wait()  # release the whole burst together
            status, body = _get(base, path)
            return status, body, path

        with ThreadPoolExecutor(max_workers=clients) as pool:
            results = list(pool.map(worker, range(clients)))

        # Single-flight: the entire burst shares one k8s load within the TTL.
        assert loads == 1, f"cache stampede: {loads} loads for one burst"
        for status, body, path in results:
            assert status == 200
            assert body  # complete snapshot, never an empty/partial window
            if path == "/api/nodes":
                payload = json.loads(body.decode("utf-8"))
                assert payload["summary"]["total"] == NODE_COUNT
                assert len(payload["nodes"]) == NODE_COUNT

        # Force TTL expiry deterministically (no wall-clock race): the next
        # request must reload exactly once more, not stampede.
        Handler.cache._at = 0.0
        status, body = _get(base, "/api/nodes")
        assert status == 200
        assert loads == 2, f"expected one reload after expiry, saw {loads} total loads"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
