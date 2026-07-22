"""Fleet-scale concurrency smoke tests for the RedfishEndpoint controller.

kopf dispatches the sync poll handler on a ThreadPoolExecutor, so at fleet scale
many endpoints reconcile at once. There is no cluster or BMC here, so this module
builds a *fake kopf engine*:

* :class:`FakeApiServer` — an in-memory CR store keyed by ``(namespace, name)``
  with a monotonic ``resourceVersion``. ``patch_status`` computes a real RFC 7386
  merge-patch and raises :class:`Conflict` when the caller's resourceVersion is
  stale, exactly like the real ``/status`` subresource under optimistic
  concurrency. Any write (spec or status) bumps the version.
* :func:`reconcile_once` — wraps the handler the way kopf does: fetch, run the
  handler against a fresh ``patch``, then apply it with a re-fetch+retry loop on
  conflict, serialized per object.
* A fake BMC facade (:class:`FakeManager` + patched ``get_system``/``get_sensors``/
  ``get_thermal``) that returns per-address readings and can hang or fail, and
  tracks pooled-session open/close so socket leaks are observable.

Every test is offline: no cluster, no network, no live BMC.

Author Mus <spyroot@gmail.com>
"""

from __future__ import annotations

import base64
import copy
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from redfish_ctl import kube_client
from redfish_ctl.api import SystemStatus, TemperatureReading, ThermalStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_MODULE = REPO_ROOT / "k8s" / "controller" / "redfish_endpoint_controller.py"


def _load_controller_module():
    spec = importlib.util.spec_from_file_location(
        "redfish_endpoint_controller_concurrency",
        CONTROLLER_MODULE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    mod = _load_controller_module()
    kube_client.reset_client_cache()
    yield mod
    kube_client.reset_client_cache()


# ---------------------------------------------------------------------------
# Fake kopf engine
# ---------------------------------------------------------------------------


class Conflict(Exception):
    """Raised by patch_status when the caller's resourceVersion is stale."""


def apply_merge_patch(target, patch):
    """RFC 7386 JSON merge-patch: null deletes, dicts recurse, omitted keys stay."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = apply_merge_patch(result.get(key, {}), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class FakeApiServer:
    """In-memory RedfishEndpoint store with optimistic-concurrency status writes."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}
        self._cr_locks: dict[tuple[str, str], threading.Lock] = {}
        self._struct_lock = threading.Lock()
        self.patch_calls = 0
        self.conflict_count = 0

    def create(self, ns, name, spec, status=None):
        with self._struct_lock:
            self._store[(ns, name)] = {
                "spec": copy.deepcopy(spec),
                "status": copy.deepcopy(status or {}),
                "rv": 1,
            }
            self._cr_locks.setdefault((ns, name), threading.Lock())

    def cr_lock(self, ns, name) -> threading.Lock:
        with self._struct_lock:
            return self._cr_locks.setdefault((ns, name), threading.Lock())

    def get(self, ns, name):
        with self._struct_lock:
            rec = self._store[(ns, name)]
            return copy.deepcopy(rec["spec"]), copy.deepcopy(rec["status"]), rec["rv"]

    def set_spec(self, ns, name, spec):
        """Update spec and bump resourceVersion (a competing writer)."""
        with self._struct_lock:
            rec = self._store[(ns, name)]
            rec["spec"] = copy.deepcopy(spec)
            rec["rv"] += 1

    def patch_status(self, ns, name, resource_version, status_patch):
        with self._struct_lock:
            self.patch_calls += 1
            rec = self._store[(ns, name)]
            if rec["rv"] != resource_version:
                self.conflict_count += 1
                raise Conflict(
                    f"{ns}/{name}: stale resourceVersion "
                    f"{resource_version} != {rec['rv']}"
                )
            rec["status"] = apply_merge_patch(rec["status"], status_patch)
            rec["rv"] += 1
            return rec["rv"]

    def status_of(self, ns, name):
        with self._struct_lock:
            return copy.deepcopy(self._store[(ns, name)]["status"])

    def names(self):
        with self._struct_lock:
            return list(self._store)


class ConcurrencyTracker:
    """Records the peak number of handlers running for each CR key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], int] = {}
        self.peak: dict[tuple[str, str], int] = {}

    def enter(self, key):
        with self._lock:
            self._active[key] = self._active.get(key, 0) + 1
            self.peak[key] = max(self.peak.get(key, 0), self._active[key])

    def exit(self, key):
        with self._lock:
            self._active[key] -= 1

    def global_peak(self) -> int:
        return max(self.peak.values(), default=0)


def reconcile_once(module, server, ns, name, *, max_retries=8, tracker=None, before_patch=None):
    """Drive the handler like kopf: fetch, handle, apply status with retry-on-conflict.

    Serialized per object via the CR lock, matching kopf's guarantee that a single
    resource is never handled by two workers at once.
    """
    key = (ns, name)
    with server.cr_lock(ns, name):
        for attempt in range(max_retries):
            spec, status, resource_version = server.get(ns, name)
            patch: dict = {}
            if tracker is not None:
                tracker.enter(key)
            try:
                module.poll_redfish_endpoint(
                    spec=spec,
                    body={"spec": spec, "status": status},
                    namespace=ns,
                    name=name,
                    patch=patch,
                    status=status,
                    logger=None,
                )
            finally:
                if tracker is not None:
                    tracker.exit(key)
            status_patch = patch.get("status")
            if not status_patch:  # poll skipped (not due) — nothing to persist
                return patch
            if before_patch is not None:
                before_patch(attempt)  # test hook: inject a competing write
            try:
                server.patch_status(ns, name, resource_version, status_patch)
                return patch
            except Conflict:
                continue
        raise AssertionError(f"reconcile of {ns}/{name} exhausted {max_retries} retries")


# ---------------------------------------------------------------------------
# Fake BMC facade
# ---------------------------------------------------------------------------


class SessionRegistry:
    """Counts pooled-session opens/closes so leaks are observable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.opened = 0
        self.closed = 0
        self.peak_live = 0
        self.built_addresses: list[str] = []

    def opened_session(self, address):
        with self._lock:
            self.opened += 1
            self.built_addresses.append(address)
            self.peak_live = max(self.peak_live, self.opened - self.closed)

    def closed_session(self):
        with self._lock:
            self.closed += 1

    @property
    def live(self) -> int:
        with self._lock:
            return self.opened - self.closed


class FakeSession:
    def __init__(self, registry: SessionRegistry, address: str) -> None:
        self._registry = registry
        self._closed = False
        registry.opened_session(address)

    def close(self):
        if not self._closed:
            self._closed = True
            self._registry.closed_session()


class FakeManager:
    """Stand-in for IDracManager: records creds and a closable session."""

    def __init__(self, *, registry, behaviors, hang_event, idrac_ip, idrac_username,
                 idrac_password, **_):
        self.address = idrac_ip
        self.username = idrac_username
        self.password = idrac_password
        self.behavior = behaviors.get(idrac_ip, "ok")
        self._hang_event = hang_event
        self._session_cache = FakeSession(registry, idrac_ip)


def _address_temp(address: str) -> float:
    """Deterministic per-address temperature so cross-contamination is detectable."""
    digits = "".join(ch for ch in address if ch.isdigit())
    return float(int(digits)) if digits else -1.0


def install_fake_bmc(monkeypatch, module, *, behaviors, hang_event=None):
    """Wire a fake BMC facade into the controller module. Returns a SessionRegistry."""
    registry = SessionRegistry()

    def factory(**kwargs):
        return FakeManager(
            registry=registry,
            behaviors=behaviors,
            hang_event=hang_event,
            **kwargs,
        )

    def _run_behavior(manager):
        if manager.behavior == "error":
            raise ConnectionError(f"BMC unreachable: {manager.address}")
        if manager.behavior in {"slow", "hang"} and manager._hang_event is not None:
            # Bounded wait so a wedged test can never hang CI forever.
            manager._hang_event.wait(timeout=30)

    def fake_get_system(manager):
        _run_behavior(manager)
        return SystemStatus(
            id=manager.address,
            name=manager.address,
            power_state=manager.address,  # echo address to detect any bleed
            health="OK",
            state="Enabled",
            raw={},
        )

    def fake_get_sensors(manager):
        return ()

    def fake_get_thermal(manager):
        reading = TemperatureReading(
            "Chassis_0", "Inlet", "Intake", _address_temp(manager.address), "/s/1", {}
        )
        return ThermalStatus(summary={}, temperatures=(reading,), fans=(), raw={})

    monkeypatch.setattr(module, "MANAGER_FACTORY", factory)
    monkeypatch.setattr(module, "get_system", fake_get_system)
    monkeypatch.setattr(module, "get_sensors", fake_get_sensors)
    monkeypatch.setattr(module, "get_thermal", fake_get_thermal)
    return registry


def install_counting_secret_client(monkeypatch, creds_holder):
    """Route load_secret_credentials through the shared client with counting seams.

    ``creds_holder`` is a one-item list ``[{"username":..., "password":...}]`` that
    tests may mutate to simulate rotation. Returns a dict of counters.
    """
    counters = {"config_loads": 0, "secret_reads": 0}
    lock = threading.Lock()

    class FakeSecret:
        def __init__(self, creds):
            self.data = {
                "username": base64.b64encode(creds["username"].encode()).decode(),
                "password": base64.b64encode(creds["password"].encode()).decode(),
            }

    class FakeApi:
        def read_namespaced_secret(self, name, namespace):
            with lock:
                counters["secret_reads"] += 1
            return FakeSecret(creds_holder[0])

    def fake_load():
        with lock:
            counters["config_loads"] += 1

    monkeypatch.setattr(kube_client, "_load_kube_config", fake_load)
    monkeypatch.setattr(kube_client, "_build_core_v1_api", lambda: FakeApi())
    return counters


def _endpoint_spec(address, **overrides):
    spec = {
        "address": address,
        "port": 443,
        "insecure": True,
        "pollInterval": "30s",
        "secretRef": {"name": "bmc-login"},
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_200_endpoints_reconcile_concurrently_without_cross_contamination(monkeypatch, module):
    """200 CRs polled concurrently (pool 50): each status reflects only its own BMC."""
    server = FakeApiServer()
    registry = install_fake_bmc(monkeypatch, module, behaviors={})
    creds = [{"username": "root", "password": "pw"}]
    counters = install_counting_secret_client(monkeypatch, creds)
    tracker = ConcurrencyTracker()

    names = [f"node-{i:03d}" for i in range(200)]
    for i, name in enumerate(names):
        server.create("fleet", name, _endpoint_spec(f"bmc-{i:03d}"))

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [
            pool.submit(reconcile_once, module, server, "fleet", name, tracker=tracker)
            for name in names
        ]
        for future in futures:
            future.result()

    for i, name in enumerate(names):
        status = server.status_of("fleet", name)
        address = f"bmc-{i:03d}"
        assert status["powerState"] == address, f"{name} got another BMC's power state"
        assert status["temperature"]["maxCelsius"] == float(i)
        assert status["conditions"][0]["status"] == "True"
        assert status["consecutiveFailures"] == 0

    # Shared client: one config load for the whole fleet; one secret read per CR.
    assert counters["config_loads"] == 1
    assert counters["secret_reads"] == 200
    # No CR handled by two workers at once.
    assert tracker.global_peak() == 1
    # Every pooled session opened was closed — no socket leak.
    assert registry.opened == 200
    assert registry.closed == 200


def test_conflict_on_status_write_is_retried_not_dropped(monkeypatch, module):
    """A stale-resourceVersion 409 triggers a re-fetch+retry; the poll is not lost."""
    server = FakeApiServer()
    install_fake_bmc(monkeypatch, module, behaviors={})
    install_counting_secret_client(monkeypatch, [{"username": "root", "password": "pw"}])
    server.create("fleet", "node-x", _endpoint_spec("bmc-042"))

    injected = {"done": False}

    def bump_once(attempt):
        # On the first attempt, a competing writer bumps the resourceVersion
        # between our get() and patch_status(), forcing a single conflict.
        if not injected["done"]:
            injected["done"] = True
            server.set_spec("fleet", "node-x", _endpoint_spec("bmc-042", insecure=False))

    reconcile_once(module, server, "fleet", "node-x", before_patch=bump_once)

    assert server.conflict_count == 1, "expected exactly one forced conflict"
    status = server.status_of("fleet", "node-x")
    # The retried poll's result is what landed — status was persisted, not dropped.
    assert status["powerState"] == "bmc-042"
    assert status["conditions"][0]["status"] == "True"


def test_failing_bmc_does_not_starve_pool_or_leak_sockets(monkeypatch, module):
    """One unreachable BMC records an error + backoff; the other CRs still complete."""
    server = FakeApiServer()
    behaviors = {"bmc-005": "error"}
    registry = install_fake_bmc(monkeypatch, module, behaviors=behaviors)
    install_counting_secret_client(monkeypatch, [{"username": "root", "password": "pw"}])

    names = [f"node-{i:03d}" for i in range(20)]
    for i, name in enumerate(names):
        server.create("fleet", name, _endpoint_spec(f"bmc-{i:03d}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            name: pool.submit(reconcile_once, module, server, "fleet", name)
            for name in names
        }
        for name, future in futures.items():
            future.result()  # none hang — the failing one returns an error status

    healthy = server.status_of("fleet", "node-000")
    assert healthy["powerState"] == "bmc-000"
    assert healthy["conditions"][0]["status"] == "True"

    failed = server.status_of("fleet", "node-005")
    assert failed["conditions"][0]["status"] == "False"
    assert failed["conditions"][0]["reason"] == "BMCUnreachable"
    assert failed["consecutiveFailures"] == 1
    assert failed["nextPollAfter"]  # backoff recorded
    assert "powerState" not in failed  # no readings written on failure

    # Both the success and the error path close their pooled session.
    assert registry.opened == 20
    assert registry.closed == 20


def test_hanging_bmc_does_not_block_other_endpoints(monkeypatch, module):
    """A wedged BMC in-flight does not stop the rest of the fleet from finishing."""
    server = FakeApiServer()
    hang_event = threading.Event()
    behaviors = {"bmc-000": "hang"}
    registry = install_fake_bmc(
        monkeypatch, module, behaviors=behaviors, hang_event=hang_event
    )
    install_counting_secret_client(monkeypatch, [{"username": "root", "password": "pw"}])

    names = [f"node-{i:03d}" for i in range(12)]
    for i, name in enumerate(names):
        server.create("fleet", name, _endpoint_spec(f"bmc-{i:03d}"))

    with ThreadPoolExecutor(max_workers=6) as pool:
        hanging = pool.submit(reconcile_once, module, server, "fleet", "node-000")
        others = {
            name: pool.submit(reconcile_once, module, server, "fleet", name)
            for name in names[1:]
        }
        # The other endpoints finish while node-000 is still wedged.
        for name, future in others.items():
            future.result(timeout=20)
            assert server.status_of("fleet", name)["powerState"] == f"bmc-{names.index(name):03d}"
        assert not hanging.done()

        hang_event.set()  # release the wedged poll
        hanging.result(timeout=20)

    # Even the wedged endpoint eventually closed its session.
    assert registry.opened == registry.closed == 12


def test_rapid_spec_churn_same_cr_serialized_latest_spec_wins(monkeypatch, module):
    """Concurrent churn on one CR is serialized; the last committed spec wins."""
    server = FakeApiServer()
    install_fake_bmc(monkeypatch, module, behaviors={})
    install_counting_secret_client(monkeypatch, [{"username": "root", "password": "pw"}])
    # Polls always due so every churn iteration actually reconciles.
    monkeypatch.setattr(module, "poll_due", lambda *a, **k: True)
    server.create("fleet", "node-churn", _endpoint_spec("bmc-000"))
    tracker = ConcurrencyTracker()

    def churn(i):
        server.set_spec("fleet", "node-churn", _endpoint_spec(f"bmc-{i:03d}"))
        reconcile_once(module, server, "fleet", "node-churn", tracker=tracker)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(churn, range(1, 41)))

    # Serialized: the CR was never handled by two workers at once.
    assert tracker.peak[("fleet", "node-churn")] == 1
    # A final quiescent reconcile reflects the current (latest) spec.
    spec, _status, _rv = server.get("fleet", "node-churn")
    reconcile_once(module, server, "fleet", "node-churn")
    status = server.status_of("fleet", "node-churn")
    assert status["powerState"] == spec["address"]
    # Internally consistent: power state and temperature come from the same poll.
    assert status["temperature"]["maxCelsius"] == _address_temp(spec["address"])


def test_secret_rotation_mid_poll_uses_consistent_snapshot(monkeypatch, module):
    """Credentials are snapshotted per poll; a rotation during a poll is not torn."""
    server = FakeApiServer()
    creds = [{"username": "root", "password": "old-pw"}]
    install_counting_secret_client(monkeypatch, creds)
    monkeypatch.setattr(module, "poll_due", lambda *a, **k: True)

    registry = SessionRegistry()
    built: list[FakeManager] = []

    def factory(**kwargs):
        manager = FakeManager(registry=registry, behaviors={}, hang_event=None, **kwargs)
        built.append(manager)
        return manager

    monkeypatch.setattr(module, "MANAGER_FACTORY", factory)

    def rotating_get_system(manager):
        # Rotate the Secret *after* the manager was built for this poll: the poll
        # must keep using the snapshot it read at the start.
        creds[0] = {"username": "root", "password": "new-pw"}
        return SystemStatus(manager.address, manager.address, "On", "OK", "Enabled", {})

    monkeypatch.setattr(module, "get_system", rotating_get_system)
    monkeypatch.setattr(module, "get_sensors", lambda m: ())
    monkeypatch.setattr(
        module,
        "get_thermal",
        lambda m: ThermalStatus(summary={}, temperatures=(), fans=(), raw={}),
    )

    server.create("fleet", "node-rot", _endpoint_spec("bmc-000"))

    reconcile_once(module, server, "fleet", "node-rot")
    assert built[-1].password == "old-pw"  # poll 1 used the pre-rotation snapshot

    reconcile_once(module, server, "fleet", "node-rot")
    assert built[-1].password == "new-pw"  # poll 2 picks up the rotated Secret

    assert server.status_of("fleet", "node-rot")["conditions"][0]["status"] == "True"
    assert registry.opened == registry.closed == 2  # no leaked session across rotation


def test_1000_endpoint_fleet_stress_bounded_calls_and_no_leaks(monkeypatch, module):
    """1000 CRs: bounded API calls, one shared client, every session closed."""
    server = FakeApiServer()
    registry = install_fake_bmc(monkeypatch, module, behaviors={})
    counters = install_counting_secret_client(monkeypatch, [{"username": "r", "password": "p"}])

    fleet = 1000
    names = [f"node-{i:04d}" for i in range(fleet)]
    for i, name in enumerate(names):
        server.create("fleet", name, _endpoint_spec(f"bmc-{i:04d}"))

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [
            pool.submit(reconcile_once, module, server, "fleet", name) for name in names
        ]
        for future in futures:
            future.result()

    # One manager (and one pooled session) per CR poll — nothing unbounded.
    assert len(registry.built_addresses) == fleet
    assert registry.opened == fleet
    assert registry.closed == fleet  # stable FDs: everything opened was closed
    assert registry.live == 0
    assert registry.peak_live <= 50  # concurrency bounded by the pool
    # Shared client: kube config loaded exactly once for the whole 1000-CR fleet.
    assert counters["config_loads"] == 1
    assert counters["secret_reads"] == fleet
    # Every endpoint got a healthy status.
    assert all(
        server.status_of("fleet", name)["conditions"][0]["status"] == "True"
        for name in names
    )


# ---------------------------------------------------------------------------
# P1 (VERIFY) — merge-patch preserves prior good readings on a transient failure.
#
# Pro's P1 claimed a transient sub-read failure wipes a status field. This
# controller has no networkFirmware field, but the same principle applies to the
# readings it does write: a failed poll must not clobber the last good
# powerState/health/temperature. Confirmed here end-to-end through the fake
# merge-patch engine (the real /status subresource is also RFC 7386).
# ---------------------------------------------------------------------------


def test_transient_read_failure_retains_prior_good_readings(monkeypatch, module):
    """Poll 1 succeeds; poll 2's BMC read raises → poll 1's readings are retained."""
    server = FakeApiServer()
    behaviors = {"bmc-007": "ok"}
    install_fake_bmc(monkeypatch, module, behaviors=behaviors)
    install_counting_secret_client(monkeypatch, [{"username": "root", "password": "pw"}])
    monkeypatch.setattr(module, "poll_due", lambda *a, **k: True)
    server.create("fleet", "node-flap", _endpoint_spec("bmc-007"))

    # Poll 1: healthy.
    reconcile_once(module, server, "fleet", "node-flap")
    good = server.status_of("fleet", "node-flap")
    assert good["powerState"] == "bmc-007"
    assert good["temperature"]["maxCelsius"] == 7.0
    assert good["conditions"][0]["status"] == "True"

    # Poll 2: the thermal read raises mid-poll.
    def boom_thermal(manager):
        raise ConnectionError("thermal read failed")

    monkeypatch.setattr(module, "get_thermal", boom_thermal)
    reconcile_once(module, server, "fleet", "node-flap")

    after = server.status_of("fleet", "node-flap")
    # The readings from poll 1 survive the failed poll (merge-patch preserves them).
    assert after["powerState"] == "bmc-007"
    assert after["temperature"]["maxCelsius"] == 7.0
    assert after["lastPolled"] == good["lastPolled"]  # not advanced by the failure
    # ...while the failure is surfaced, not silent.
    assert after["conditions"][0]["status"] == "False"
    assert after["consecutiveFailures"] == 1
    assert after["lastError"] == "thermal read failed"
