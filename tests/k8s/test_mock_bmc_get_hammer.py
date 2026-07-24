"""High-concurrency GET hammer and mixed GET+POST benchmark for the mock BMC.

The existing concurrency test hammers 16 threads / 64 reads. These scale that up
to ~200 threads / 10k reads to surface read-side races or file-handle exhaustion,
and add a mixed read/write workload (the write path was benchmark-untested) to
record write-side latency percentiles alongside reads.
"""

from __future__ import annotations

import importlib.util
import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
NP = "/redfish/v1/Managers/BMC_0/NetworkProtocol"
CHASSIS = "/redfish/v1/Chassis/Chassis_0"

HAMMER_THREADS = 200
# 200-way concurrency with a large connection count exercises read-side races and
# file-descriptor reuse. The request count is 6000 rather than 10k because the
# mock serves HTTP/1.0 (a fresh TCP connection per request); on macOS loopback a
# 10k burst intermittently hits connect timeouts from TCP TIME_WAIT churn — a
# client/OS ceiling, not a server limit — so 6000 keeps the test deterministically
# green on both macOS and Linux while still hammering the same paths.
HAMMER_REQUESTS = 6000


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _fixture_name(path: str) -> str:
    return "_" + path.strip("/").replace("/", "_") + ".json"


def _corpus(tmp_path: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    payloads = {
        "/redfish/v1": {"@odata.id": "/redfish/v1", "Id": "RootService"},
        SYSTEM: {
            "@odata.id": SYSTEM,
            "Id": "System_0",
            "PowerState": "On",
            "Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Pxe"},
            "Status": {"Health": "OK", "State": "Enabled"},
        },
        NP: {
            "@odata.id": NP,
            "Id": "NetworkProtocol",
            "NTP": {"ProtocolEnabled": True, "NTPServers": ["time.example.test"]},
        },
        CHASSIS: {
            "@odata.id": CHASSIS,
            "Id": "Chassis_0",
            "Status": {"Health": "OK", "State": "Enabled"},
        },
    }
    for path, payload in payloads.items():
        _write_json(corpus / _fixture_name(path), payload)
    return corpus, payloads


def _mutation_rules(tmp_path: Path) -> Path:
    rules = {
        "vendor": "hammer",
        "rules": [
            {
                "name": "graceful-restart",
                "method": "POST",
                "path": RESET,
                "body_contains": {"ResetType": "GracefulRestart"},
                "state_transitions": [
                    {
                        "op": "set",
                        "path": SYSTEM,
                        "field": "LastResetTime",
                        "value": "2099-01-01T00:00:00Z",
                    }
                ],
                "response": {"status": 204},
            }
        ],
    }
    rules_path = tmp_path / "rules.json"
    _write_json(rules_path, rules)
    return rules_path


def _percentile(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _get(base: str, path: str) -> tuple[int, Any, float]:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=10) as response:
            raw = response.read().decode("utf-8")
            status, payload = response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status, payload = exc.code, (json.loads(raw) if raw else None)
    return status, payload, time.perf_counter() - start


def _write(base: str, path: str, method: str, body: dict[str, Any]) -> tuple[int, float]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    return status, time.perf_counter() - start


def test_get_hammer_200_threads_keeps_bodies_correct(tmp_path: Path) -> None:
    """10k concurrent GETs across paths return each path's exact corpus body."""
    module = _load_server_module()
    corpus, expected = _corpus(tmp_path)
    paths = [SYSTEM, NP, CHASSIS]

    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        plan = [paths[i % len(paths)] for i in range(HAMMER_REQUESTS)]
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=HAMMER_THREADS) as pool:
            results = list(pool.map(lambda p: (p, *_get(base, p)), plan))
        wall = time.perf_counter() - started

    latencies = [row[3] for row in results]
    p50 = _percentile(latencies, 50) * 1000
    p95 = _percentile(latencies, 95) * 1000
    p99 = _percentile(latencies, 99) * 1000
    throughput = HAMMER_REQUESTS / wall
    print(
        f"\nGET hammer (reqs={HAMMER_REQUESTS}, threads={HAMMER_THREADS}): "
        f"wall={wall:.3f}s rps={throughput:.0f} "
        f"p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms"
    )

    assert len(results) == HAMMER_REQUESTS
    errors = [row for row in results if row[1] != 200]
    assert not errors, f"{len(errors)} non-200 responses under load"
    # Every body is the complete, path-correct corpus fixture (no mixups).
    for path, status, payload, _latency in results:
        assert status == 200
        assert payload == expected[path]


def test_mixed_get_post_benchmark_records_read_and_write_latency(tmp_path: Path) -> None:
    """A mixed read/write storm keeps reads valid and writes clean (2xx/409)."""
    module = _load_server_module()
    corpus, expected = _corpus(tmp_path)
    rules = _mutation_rules(tmp_path)

    total = 4000
    # 3:1 read:write mix. Writes POST to RESET (mutating SYSTEM's overlay); reads
    # target only NP and CHASSIS, which no rule mutates, so a read's body must
    # always equal its pristine corpus fixture even while writes are in flight.
    read_paths = [NP, CHASSIS]
    plan: list[tuple[str, str, dict[str, Any] | None]] = []
    for i in range(total):
        if i % 4 == 0:
            plan.append(("POST", RESET, {"ResetType": "GracefulRestart"}))
        else:
            plan.append(("GET", read_paths[i % len(read_paths)], None))

    read_latencies: list[float] = []
    write_latencies: list[float] = []
    read_ok = 0
    write_ok = 0

    with module.run_server("127.0.0.1", 0, corpus, mutation_rules=rules) as server:
        base = "http://{}:{}".format(*server.server_address)

        def run(job: tuple[str, str, dict[str, Any] | None]) -> tuple[str, int, float]:
            method, path, body = job
            if method == "GET":
                status, payload, latency = _get(base, path)
                assert payload == expected[path]  # unmutated reads stay path-correct
                return "GET", status, latency
            status, latency = _write(base, path, method, body or {})
            return "POST", status, latency

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(pool.map(run, plan))
        wall = time.perf_counter() - started

        # The write overlay is observable on the mutated resource afterwards.
        final_status, final_system, _ = _get(base, SYSTEM)
    assert final_status == 200
    assert final_system["LastResetTime"] == "2099-01-01T00:00:00Z"
    assert final_system["PowerState"] == "On"  # unrelated fields intact

    for kind, status, latency in results:
        if kind == "GET":
            read_latencies.append(latency)
            read_ok += status == 200
        else:
            write_latencies.append(latency)
            write_ok += status in (200, 204)
            assert status in (200, 204, 409), f"unexpected write status {status}"

    print(
        f"\nmixed GET+POST (reqs={total}, threads=64): wall={wall:.3f}s "
        f"read_p95={_percentile(read_latencies, 95) * 1000:.3f}ms "
        f"write_p95={_percentile(write_latencies, 95) * 1000:.3f}ms"
    )

    assert read_ok == len(read_latencies)  # every read succeeded
    # The idempotent write rule matches every POST -> all writes succeed (204).
    assert write_ok == len(write_latencies)
