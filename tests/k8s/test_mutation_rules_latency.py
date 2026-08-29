"""Write-latency contract for the MutationRules engine under concurrency.

A rule with a ``when`` precondition forces the engine to read the corpus fixture
for that resource (file read + JSON parse). If that lookup runs while the engine
lock is held, every concurrent write serializes behind one another's disk I/O —
p95/p99 climb linearly with write concurrency. The engine must fetch corpus
state BEFORE taking the lock, so concurrent writers overlap their I/O. This test
drives N concurrent writes through a deliberately slow corpus lookup and asserts
the wall-clock stays far below the serialized lower bound (N x delay). It fails
while the lookup is under the lock and passes once it is lifted out.
"""

from __future__ import annotations

import importlib.util
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"

# Per-lookup corpus-read delay and writer count. The serialized lower bound is
# WRITERS * LOOKUP_DELAY; overlapped I/O collapses to roughly one delay.
LOOKUP_DELAY = 0.02
WRITERS = 12
SERIALIZED_LOWER_BOUND = WRITERS * LOOKUP_DELAY


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _percentile(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _reset_when_on_rules() -> dict[str, Any]:
    return {
        "vendor": "latency-test",
        "rules": [
            {
                "name": "reset-when-on",
                "method": "POST",
                "path": RESET,
                "body_contains": {"ResetType": "GracefulRestart"},
                # Precondition read against System_0's corpus state -> forces a
                # corpus lookup on every matching write.
                "when": [
                    {"path": SYSTEM, "field": "PowerState", "equals": "On"},
                ],
                "state_transitions": [
                    {
                        "op": "set",
                        "path": SYSTEM,
                        "field": "LastResetTime",
                        "value": "2099-01-01T00:00:00Z",
                    },
                ],
                "response": {"status": 204},
            },
        ],
    }


def test_mutation_write_corpus_io_does_not_serialize_under_lock() -> None:
    """Concurrent writes overlap their corpus I/O instead of queuing on the lock."""
    module = _load_server_module()
    engine = module.MutationRules(_reset_when_on_rules())

    lookups = 0
    lookups_lock = threading.Lock()

    def slow_lookup(_path: str) -> dict[str, Any]:
        nonlocal lookups
        with lookups_lock:
            lookups += 1
        time.sleep(LOOKUP_DELAY)  # emulate a slow corpus file read + JSON parse
        return {"PowerState": "On"}

    latencies: list[float] = []
    latencies_lock = threading.Lock()

    def one_write() -> dict[str, Any] | None:
        start = time.perf_counter()
        result = engine.match_write(
            "POST", RESET, {"ResetType": "GracefulRestart"}, corpus_state=slow_lookup
        )
        with latencies_lock:
            latencies.append(time.perf_counter() - start)
        return result

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        results = list(pool.map(lambda _: one_write(), range(WRITERS)))
    wall = time.perf_counter() - started

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    print(
        f"\nmutation write latency (n={WRITERS}, lookup_delay={LOOKUP_DELAY}s): "
        f"wall={wall:.3f}s p50={p50:.3f}s p95={p95:.3f}s p99={p99:.3f}s "
        f"lookups={lookups} serialized_bound={SERIALIZED_LOWER_BOUND:.3f}s"
    )

    # Correctness must be preserved: every matching write applied exactly once.
    assert all(r == {"status": 204} for r in results)
    assert engine.status()["applied"].count("reset-when-on") == WRITERS
    # The overlay reflects the serialized transitions (torn state would differ).
    assert engine.overlay_for(SYSTEM)["LastResetTime"] == "2099-01-01T00:00:00Z"

    # The crux: if corpus I/O ran under the lock, wall-clock would be >=
    # SERIALIZED_LOWER_BOUND. Overlapped I/O collapses it to ~one delay.
    assert wall < SERIALIZED_LOWER_BOUND / 2, (
        f"writes serialized on corpus I/O: wall={wall:.3f}s is not below "
        f"half the serialized bound {SERIALIZED_LOWER_BOUND:.3f}s"
    )
    # Per-write latency should track a single delay, not a queue of them.
    assert p95 < SERIALIZED_LOWER_BOUND / 2
