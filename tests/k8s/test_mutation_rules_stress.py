"""High-concurrency stress + failure-injection oracle for MutationRules.

Fires a fixed 5000-write multiset through the engine on 200 threads, with one
rule injecting stochastic failures and one rule gated by a corpus precondition,
then replays the identical multiset serially on a fresh engine seeded the same
way. The workload is built so its observable aggregates are order-invariant:

* each write matches exactly one rule (no overlapping globs), so ``applied`` per
  rule is fixed by how many writes target it;
* every success rule sets a distinct field to a fixed value, so the final
  overlay is the commutative union of those fields regardless of order;
* only the flaky rule draws the seeded RNG, once per matching write, so the
  number of injected failures is the count of ``rng() < p`` over the first
  ``N_fail`` seeded draws — identical for the concurrent and serial runs even
  though which specific writes fail differs.

The concurrent engine must therefore match the serial oracle exactly (applied
counts, failed counts, and per-resource overlays), proving the engine lock keeps
transitions atomic and the RNG uncorrupted under load. Latency percentiles are
recorded for the write path.
"""

from __future__ import annotations

import importlib.util
import math
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"

SYSTEM = "/redfish/v1/Systems/System_0"
RESET = f"{SYSTEM}/Actions/ComputerSystem.Reset"
NP = "/redfish/v1/Managers/BMC_0/NetworkProtocol"
CHASSIS = "/redfish/v1/Chassis/Chassis_0"

SEED = 90210
THREADS = 200
PER_SUCCESS_RULE = 600
FAIL_WRITES = 1600
NOMATCH_WRITES = 1600
FAILURE_PROBABILITY = 0.3


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


def _rules() -> dict[str, Any]:
    return {
        "vendor": "stress",
        "rules": [
            {
                "name": "sys-tag",
                "method": "PATCH",
                "path": SYSTEM,
                "body_contains": {"AssetTag": "svc"},
                # Corpus-stable precondition -> always holds, but exercises the
                # precondition + corpus-lookup path (the one Fix #2 lifts out).
                "when": [{"path": SYSTEM, "field": "PowerState", "equals": "On"}],
                "state_transitions": [
                    {"op": "set", "path": SYSTEM, "field": "AssetTag", "value": "svc"}
                ],
                "response": {"status": 200, "body": {"ok": True}},
            },
            {
                "name": "np-ntp",
                "method": "PATCH",
                "path": NP,
                "body_contains": {"NTP": {"ProtocolEnabled": False}},
                "state_transitions": [
                    {
                        "op": "set",
                        "path": NP,
                        "json_path": ["NTP", "ProtocolEnabled"],
                        "value": False,
                    }
                ],
                "response": {"status": 200, "body": {"ok": True}},
            },
            {
                "name": "chs-led",
                "method": "PATCH",
                "path": CHASSIS,
                "body_contains": {"IndicatorLED": "Lit"},
                "state_transitions": [
                    {"op": "set", "path": CHASSIS, "field": "IndicatorLED", "value": "Lit"}
                ],
                "response": {"status": 200, "body": {"ok": True}},
            },
            {
                "name": "reset-flaky",
                "method": "POST",
                "path": RESET,
                "body_contains": {"ResetType": "ForceRestart"},
                "failure": {"probability": FAILURE_PROBABILITY, "response": {"status": 503}},
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
        ],
    }


def _workload() -> list[tuple[str, str, dict[str, Any]]]:
    writes: list[tuple[str, str, dict[str, Any]]] = []
    writes += [("PATCH", SYSTEM, {"AssetTag": "svc"})] * PER_SUCCESS_RULE
    writes += [("PATCH", NP, {"NTP": {"ProtocolEnabled": False}})] * PER_SUCCESS_RULE
    writes += [("PATCH", CHASSIS, {"IndicatorLED": "Lit"})] * PER_SUCCESS_RULE
    writes += [("POST", RESET, {"ResetType": "ForceRestart"})] * FAIL_WRITES
    writes += [("PATCH", SYSTEM, {"Unmatched": 1})] * NOMATCH_WRITES
    random.Random(20260715).shuffle(writes)  # fixed multiset order for dispatch
    return writes


def test_mutation_rules_200_threads_match_serial_oracle_with_failure_injection() -> None:
    """A 200-thread write storm reproduces the serial oracle's state exactly."""
    module = _load_server_module()
    normalize = module._normalize_request_path
    corpus = {normalize(SYSTEM): {"PowerState": "On"}}

    def corpus_lookup(path: str) -> dict[str, Any]:
        return corpus.get(normalize(path), {})

    writes = _workload()

    concurrent = module.MutationRules(_rules(), seed=SEED)
    latencies: list[float] = []
    latencies_lock = threading.Lock()

    def one(write: tuple[str, str, dict[str, Any]]) -> Any:
        method, path, body = write
        start = time.perf_counter()
        result = concurrent.match_write(method, path, body, corpus_state=corpus_lookup)
        elapsed = time.perf_counter() - start
        with latencies_lock:
            latencies.append(elapsed)
        return result

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        responses = list(pool.map(one, writes))
    wall = time.perf_counter() - started

    # Serial oracle: identical multiset, fresh engine, same seed.
    serial = module.MutationRules(_rules(), seed=SEED)
    for method, path, body in writes:
        serial.match_write(method, path, body, corpus_state=corpus_lookup)

    conc_status = concurrent.status()
    serial_status = serial.status()
    applied = Counter(conc_status["applied"])
    failed = Counter(conc_status["failed"])

    p50 = _percentile(latencies, 50) * 1000
    p95 = _percentile(latencies, 95) * 1000
    p99 = _percentile(latencies, 99) * 1000
    print(
        f"\nmutation stress (writes={len(writes)}, threads={THREADS}): "
        f"wall={wall:.3f}s p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms "
        f"applied={sum(applied.values())} failed={sum(failed.values())} "
        f"nomatch={responses.count(None)}"
    )

    # Oracle equivalence: aggregates and overlays match the serial replay exactly.
    assert applied == Counter(serial_status["applied"])
    assert failed == Counter(serial_status["failed"])
    for path in (SYSTEM, NP, CHASSIS):
        assert concurrent.overlay_for(path) == serial.overlay_for(path)

    # Exact, order-invariant expectations.
    assert applied["sys-tag"] == PER_SUCCESS_RULE
    assert applied["np-ntp"] == PER_SUCCESS_RULE
    assert applied["chs-led"] == PER_SUCCESS_RULE
    assert set(failed) == {"reset-flaky"}
    reset_failures = sum(failed.values())
    assert 0 < reset_failures < FAIL_WRITES  # injection actually fired, not all
    assert applied["reset-flaky"] == FAIL_WRITES - reset_failures
    assert responses.count(None) == NOMATCH_WRITES  # unmatched writes -> no rule

    # Final overlay carries every distinctly-set field, uncorrupted.
    assert concurrent.overlay_for(SYSTEM)["AssetTag"] == "svc"
    assert concurrent.overlay_for(SYSTEM)["LastResetTime"] == "2099-01-01T00:00:00Z"
    assert concurrent.overlay_for(NP)["NTP"]["ProtocolEnabled"] is False
    assert concurrent.overlay_for(CHASSIS)["IndicatorLED"] == "Lit"
