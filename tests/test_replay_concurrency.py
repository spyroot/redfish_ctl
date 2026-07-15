"""Concurrency contracts for the ordered ReplayState engine.

The replay engine consumes a fixed step sequence exactly once, in order. These
tests hammer it concurrently to prove two invariants that only break under
concurrency: (1) each step is matched exactly once even when many threads race
the same write (later duplicates get 409), and (2) a ``/__set_scenario`` reset is
atomic with respect to in-flight writes (no torn overlay, no 5xx), leaving the
engine behaving as if freshly started.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
SCENARIO = "power-cycle"

# The ordered trace, and the write each step matches (fired in order per thread).
STEP_WRITES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("POST", RESET, {"ResetType": "ForceOff"}),
    ("PATCH", SYSTEM, {"Boot": {"BootSourceOverrideTarget": "Hdd"}}),
    ("POST", RESET, {"ResetType": "On"}),
    ("PATCH", SYSTEM, {"AssetTag": "svc-1"}),
    ("POST", RESET, {"ResetType": "GracefulRestart"}),
)


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


def _tiny_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_json(
        corpus / _fixture_name("/redfish/v1"),
        {"@odata.id": "/redfish/v1", "Id": "RootService"},
    )
    _write_json(
        corpus / _fixture_name(SYSTEM),
        {
            "@odata.id": SYSTEM,
            "Id": "System_0",
            "PowerState": "On",
            "AssetTag": "",
            "Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Pxe"},
        },
    )
    return corpus


def _replay_trace(tmp_path: Path) -> Path:
    steps = [
        {
            "name": "force-off",
            "method": "POST",
            "path": RESET,
            "body_contains": {"ResetType": "ForceOff"},
            "state_transitions": [
                {"op": "set", "path": SYSTEM, "field": "PowerState", "value": "Off"}
            ],
            "response": {"status": 204},
        },
        {
            "name": "set-hdd",
            "method": "PATCH",
            "path": SYSTEM,
            "body_contains": {"Boot": {"BootSourceOverrideTarget": "Hdd"}},
            "state_transitions": [
                {
                    "op": "set",
                    "path": SYSTEM,
                    "json_path": ["Boot", "BootSourceOverrideTarget"],
                    "value": "Hdd",
                }
            ],
            "response": {"status": 200, "body": {"ok": True}},
        },
        {
            "name": "power-on",
            "method": "POST",
            "path": RESET,
            "body_contains": {"ResetType": "On"},
            "state_transitions": [
                {"op": "set", "path": SYSTEM, "field": "PowerState", "value": "On"}
            ],
            "response": {"status": 204},
        },
        {
            "name": "tag",
            "method": "PATCH",
            "path": SYSTEM,
            "body_contains": {"AssetTag": "svc-1"},
            "state_transitions": [
                {"op": "set", "path": SYSTEM, "field": "AssetTag", "value": "svc-1"}
            ],
            "response": {"status": 200, "body": {"ok": True}},
        },
        {
            "name": "graceful",
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
        },
    ]
    trace = {"scenario": SCENARIO, "steps": steps}
    trace_path = tmp_path / "replay.json"
    _write_json(trace_path, trace)
    return trace_path


def _request(
    base: str, path: str, method: str = "GET", body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else None)


def _run_replay(module: Any, corpus: Path, trace: Path) -> None:
    successes: list[tuple[str, str]] = []
    statuses: list[int] = []
    lock = threading.Lock()
    done = threading.Event()
    workers = 24

    with module.run_server("127.0.0.1", 0, corpus, replay_trace=trace) as server:
        base = "http://{}:{}".format(*server.server_address)

        def worker() -> None:
            rounds = 0
            while not done.is_set() and rounds < 200:
                rounds += 1
                for method, path, body in STEP_WRITES:
                    if done.is_set():
                        break
                    status, _payload = _request(base, path, method, body)
                    with lock:
                        statuses.append(status)
                        if status in (200, 204):
                            successes.append((method, path))
                            if len(successes) >= len(STEP_WRITES):
                                done.set()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in [pool.submit(worker) for _ in range(workers)]:
                future.result()

        final_status, final = _request(base, SYSTEM)
        replay_code, replay = _request(base, "/__replay_status")

    # (a) exactly one success per step, never more.
    assert len(successes) == len(STEP_WRITES)
    # (b) every non-success write is a clean 409 (no 5xx, no torn write).
    assert set(statuses) <= {200, 204, 409}
    assert statuses.count(200) + statuses.count(204) == len(STEP_WRITES)
    # (c) replay is complete and each step counted once.
    assert replay_code == 200
    assert replay["complete"] is True
    assert replay["matched_steps"] == len(STEP_WRITES)
    assert replay["pending_steps"] == []
    # (d) final overlay equals the trace's deterministic end state.
    assert final_status == 200
    assert final["PowerState"] == "On"
    assert final["Boot"]["BootSourceOverrideTarget"] == "Hdd"
    assert final["AssetTag"] == "svc-1"
    assert final["LastResetTime"] == "2099-01-01T00:00:00Z"


def test_replay_determinism_under_concurrency(tmp_path: Path) -> None:
    """Each ordered step is consumed exactly once under a 24-thread race."""
    module = _load_server_module()
    corpus = _tiny_corpus(tmp_path)
    trace = _replay_trace(tmp_path)
    _run_replay(module, corpus, trace)


def test_scenario_reset_is_atomic_under_write_load(tmp_path: Path) -> None:
    """A reset amid concurrent writes never tears state and leaves a clean engine."""
    module = _load_server_module()
    corpus = _tiny_corpus(tmp_path)
    trace = _replay_trace(tmp_path)

    statuses: list[int] = []
    lock = threading.Lock()
    stop = threading.Event()

    with module.run_server("127.0.0.1", 0, corpus, replay_trace=trace) as server:
        base = "http://{}:{}".format(*server.server_address)

        def writer() -> None:
            while not stop.is_set():
                for method, path, body in STEP_WRITES:
                    if stop.is_set():
                        break
                    status, _ = _request(base, path, method, body)
                    with lock:
                        statuses.append(status)

        def resetter() -> None:
            # Interleave several resets with the write storm.
            for _ in range(20):
                status, payload = _request(
                    base, "/__set_scenario", "POST", {"scenario": SCENARIO}
                )
                with lock:
                    statuses.append(status)
                assert status == 200, payload

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(writer) for _ in range(8)]
            futures.append(pool.submit(resetter))
            # Let the resetter finish, then stop the writers.
            futures[-1].result()
            stop.set()
            for future in futures[:-1]:
                future.result()

        # After the storm, one clean reset must return the engine to zero.
        reset_code, reset_status = _request(
            base, "/__set_scenario", "POST", {"scenario": SCENARIO}
        )
        after_reset_system_code, after_reset_system = _request(base, SYSTEM)

    # No request in the whole storm produced a server error or an unexpected code.
    assert set(statuses) <= {200, 204, 409}
    # The final clean reset zeroes the engine (atomic reset, not a torn count).
    assert reset_code == 200
    assert reset_status["matched_steps"] == 0
    assert reset_status["complete"] is False
    # Overlays are cleared: the System resource is back to its corpus baseline.
    assert after_reset_system_code == 200
    assert after_reset_system["PowerState"] == "On"
    assert after_reset_system["Boot"]["BootSourceOverrideTarget"] == "Pxe"
    assert after_reset_system["AssetTag"] == ""
    assert "LastResetTime" not in after_reset_system

    # And a freshly-reset engine still replays the full sequence deterministically.
    _run_replay(module, corpus, trace)
