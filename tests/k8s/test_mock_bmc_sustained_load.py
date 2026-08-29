"""Sustained-load stability: no file-descriptor, thread, or memory leak.

Drives a bounded but sustained mixed read/write storm (with periodic
``/__set_scenario`` resets so engine state stays bounded) and checks that open
file descriptors, live threads, and RSS return to their pre-load baseline, and
that the server stays responsive throughout. A socket/file-handle leak or a
runaway thread pool would show as a monotonic climb the assertions catch.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
SCENARIO = "sustained"
OPS = 4000
THREADS = 50


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_json(corpus / "_redfish_v1.json", {"@odata.id": "/redfish/v1", "Id": "Root"})
    _write_json(
        corpus / "_redfish_v1_Systems_System_0.json",
        {"@odata.id": SYSTEM, "Id": "System_0", "PowerState": "On", "AssetTag": ""},
    )
    return corpus


def _rules(tmp_path: Path) -> Path:
    rules = {
        "vendor": SCENARIO,
        "rules": [
            {
                "name": "graceful",
                "method": "POST",
                "path": RESET,
                "body_contains": {"ResetType": "GracefulRestart"},
                "state_transitions": [
                    {"op": "set", "path": SYSTEM, "field": "PowerState", "value": "On"}
                ],
                "response": {"status": 204},
            },
            {
                "name": "tag",
                "method": "PATCH",
                "path": SYSTEM,
                "body_contains": {"AssetTag": "svc"},
                "state_transitions": [
                    {"op": "set", "path": SYSTEM, "field": "AssetTag", "value": "svc"}
                ],
                "response": {"status": 200, "body": {"ok": True}},
            },
        ],
    }
    path = tmp_path / "rules.json"
    _write_json(path, rules)
    return path


def _request(base: str, path: str, method: str = "GET", body: Any = None) -> int:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code


def _op(index: int, base: str) -> int:
    bucket = index % 100
    if bucket < 45:
        return _request(base, SYSTEM)
    if bucket < 70:
        return _request(base, RESET, "POST", {"ResetType": "GracefulRestart"})
    if bucket < 90:
        return _request(base, SYSTEM, "PATCH", {"AssetTag": "svc"})
    if bucket == 99:
        return _request(base, "/__set_scenario", "POST", {"scenario": SCENARIO})
    return _request(base, "/redfish/v1")


def test_sustained_mixed_load_leaks_no_fds_threads_or_memory(tmp_path: Path) -> None:
    """A sustained storm returns FDs/threads/RSS to baseline and stays responsive."""
    psutil = pytest.importorskip("psutil")
    module = _load_server_module()
    corpus = _corpus(tmp_path)
    rules = _rules(tmp_path)
    proc = psutil.Process()

    with module.run_server("127.0.0.1", 0, corpus, mutation_rules=rules) as server:
        base = "http://{}:{}".format(*server.server_address)

        # Warm up so lazy imports / buffers are allocated before the baseline.
        for _ in range(50):
            _request(base, SYSTEM)
        time.sleep(0.2)
        fd_before = proc.num_fds()
        rss_before = proc.memory_info().rss
        threads_before = threading.active_count()

        statuses: list[int] = []
        statuses_lock = threading.Lock()

        def run(index: int) -> None:
            code = _op(index, base)
            with statuses_lock:
                statuses.append(code)

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(run, range(OPS)))

        # Let daemon request threads and sockets settle before re-sampling.
        time.sleep(0.5)
        fd_after = proc.num_fds()
        rss_after = proc.memory_info().rss
        threads_after = threading.active_count()

        # Server is still healthy after the storm.
        assert _request(base, SYSTEM) == 200

    print(
        f"\nsustained load (ops={OPS}, threads={THREADS}): "
        f"fd {fd_before}->{fd_after} rss {rss_before // 1024}K->{rss_after // 1024}K "
        f"threads {threads_before}->{threads_after}"
    )

    assert len(statuses) == OPS
    # Only expected codes: reads 200, writes 200/204, scenario reset 200.
    assert set(statuses) <= {200, 204}
    # No leak: descriptors, threads, and memory return near baseline.
    assert fd_after - fd_before <= 16, f"fd leak: {fd_before} -> {fd_after}"
    assert threads_after - threads_before <= 4, (
        f"thread leak: {threads_before} -> {threads_after}"
    )
    assert rss_after - rss_before <= 64 * 1024 * 1024, (
        f"rss grew {(rss_after - rss_before) // 1024}K over {OPS} ops"
    )
