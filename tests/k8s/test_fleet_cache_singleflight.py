"""Single-flight contract for the fleet-status endpoint cache.

Many dashboard clients hit the consumer at once. The short-TTL ``_EndpointCache``
exists so a burst does not fan out into one Kubernetes API call per request. If
the cache invokes ``loader()`` outside its lock, every thread that finds the
entry stale at the same instant stampedes into a parallel refill (thundering
herd). The cache must be single-flight: within one TTL window a concurrent burst
triggers at most one load. This test fires a synchronized burst against an empty
cache and asserts exactly one refill — it fails against the herd and passes once
single-flight is in place.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_MODULE = REPO_ROOT / "k8s" / "consumer" / "fleet_status_app.py"


def _load_fleet_module() -> Any:
    spec = importlib.util.spec_from_file_location("fleet_status_app", FLEET_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_endpoint_cache_single_flight_under_concurrent_burst() -> None:
    """A synchronized burst against a cold cache refills exactly once."""
    module = _load_fleet_module()
    cache = module._EndpointCache(ttl_seconds=30.0)

    clients = 48
    calls = 0
    calls_lock = threading.Lock()
    barrier = threading.Barrier(clients)

    def loader() -> list[dict[str, Any]]:
        nonlocal calls
        with calls_lock:
            calls += 1
            generation = calls
        time.sleep(0.05)  # widen the window so a herd would overlap here
        return [{"generation": generation}]

    def worker(_: int) -> list[dict[str, Any]]:
        barrier.wait()  # release all clients together for a maximal stampede
        return cache.get(loader)

    with ThreadPoolExecutor(max_workers=clients) as pool:
        results = list(pool.map(worker, range(clients)))

    # Single-flight: the whole burst shares one refill.
    assert calls == 1, f"thundering herd: loader ran {calls} times, expected 1"
    # Every client sees the same complete snapshot (no partial/empty window).
    assert all(r == [{"generation": 1}] for r in results)


def test_endpoint_cache_serves_within_ttl_and_reloads_after_expiry() -> None:
    """Fresh entries are served from cache; a fresh load happens after the TTL."""
    module = _load_fleet_module()
    cache = module._EndpointCache(ttl_seconds=0.15)

    calls = 0

    def loader() -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"generation": calls}]

    first = cache.get(loader)
    second = cache.get(loader)  # within TTL -> cached, no reload
    assert first == second == [{"generation": 1}]
    assert calls == 1

    time.sleep(0.2)  # let the TTL lapse
    third = cache.get(loader)  # stale -> one reload
    assert third == [{"generation": 2}]
    assert calls == 2


def test_endpoint_cache_recovers_after_loader_error() -> None:
    """A failed load resets the in-flight flag so the next caller can retry."""
    module = _load_fleet_module()
    cache = module._EndpointCache(ttl_seconds=30.0)

    def failing_loader() -> list[dict[str, Any]]:
        raise RuntimeError("k8s API unavailable")

    try:
        cache.get(failing_loader)
    except RuntimeError:
        pass

    # The engine must not be wedged in a permanent "loading" state.
    good = cache.get(lambda: [{"generation": 1}])
    assert good == [{"generation": 1}]
