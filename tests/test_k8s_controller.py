"""Contracts for the read-only Kubernetes RedfishEndpoint controller."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from vendor_corpus import corpus_dir

from redfish_ctl.api import (
    FanReading,
    SensorReading,
    SystemStatus,
    TemperatureReading,
    ThermalStatus,
)
from redfish_ctl.redfish_manager_base import RedfishManagerBase

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_MODULE = REPO_ROOT / "k8s" / "controller" / "redfish_endpoint_controller.py"
CRD_MANIFEST = REPO_ROOT / "k8s" / "controller" / "redfish-endpoint-crd.yaml"
GB300_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


def _load_controller_module():
    spec = importlib.util.spec_from_file_location(
        "redfish_endpoint_controller",
        CONTROLLER_MODULE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_for_path(path: str) -> Path | None:
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


class FakeSpan:
    """Small span double that records attributes set by controller handlers."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        """Record a span attribute assignment.

        :param key: span attribute key.
        :param value: span attribute value.
        """
        self.attributes[key] = value


def test_crd_schema_pins_read_only_endpoint_spec_and_status_shape() -> None:
    """The CRD exposes only connection fields and read-only status."""
    crd = yaml.safe_load(CRD_MANIFEST.read_text(encoding="utf-8"))
    version = crd["spec"]["versions"][0]
    schema = version["schema"]["openAPIV3Schema"]
    spec_props = schema["properties"]["spec"]["properties"]
    status_props = schema["properties"]["status"]["properties"]

    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["spec"]["names"]["kind"] == "RedfishEndpoint"
    assert crd["spec"]["scope"] == "Namespaced"
    assert set(spec_props) == {
        "address",
        "port",
        "insecure",
        "pollInterval",
        "secretRef",
    }
    secret_ref_props = spec_props["secretRef"]["properties"]
    assert secret_ref_props["name"]["type"] == "string"
    assert secret_ref_props["usernameKey"]["default"] == "username"
    assert secret_ref_props["passwordKey"]["default"] == "password"
    assert set(status_props) == {
        "powerState",
        "health",
        "temperature",
        "networkFirmware",
        "lastPolled",
        "conditions",
        "consecutiveFailures",
        "lastError",
        "nextPollAfter",
    }
    assert status_props["temperature"]["properties"]["maxCelsius"]["type"] == "number"
    nic_fw_props = status_props["networkFirmware"]["properties"]
    assert nic_fw_props["distinctVersions"]["items"]["type"] == "string"
    assert nic_fw_props["components"]["items"]["properties"]["version"]["type"] == "string"
    # Reachability/backoff fields the error path writes; the structural CRD must
    # allow them or the API server would prune them and the freeze stays silent.
    assert status_props["consecutiveFailures"]["type"] == "integer"
    assert status_props["lastError"]["nullable"] is True
    assert status_props["nextPollAfter"]["nullable"] is True
    condition_item = status_props["conditions"]["items"]
    assert set(condition_item["required"]) == {
        "type",
        "status",
        "reason",
        "lastTransitionTime",
    }
    assert "valueFrom" not in json.dumps(crd)
    assert version["additionalPrinterColumns"] == [
        {
            "name": "POWER",
            "type": "string",
            "jsonPath": ".status.powerState",
        },
        {
            "name": "HEALTH",
            "type": "string",
            "jsonPath": ".status.health",
        },
        {
            "name": "NIC-FW",
            "type": "integer",
            "jsonPath": ".status.networkFirmware.firmwareCount",
        },
        {
            "name": "POLLED",
            "type": "date",
            "jsonPath": ".status.lastPolled",
        },
    ]


def test_build_status_tolerates_missing_values_and_summarizes_temperatures() -> None:
    """Status rendering keeps fields optional and ignores non-numeric temperatures."""
    module = _load_controller_module()
    polled_at = datetime(2026, 7, 10, 14, 40, 0, tzinfo=timezone.utc)
    system = SystemStatus(
        id="System_0",
        name="System_0",
        power_state="On",
        health="OK",
        state="Enabled",
        raw={},
    )
    thermal = ThermalStatus(
        summary={},
        temperatures=(
            TemperatureReading("Chassis_0", "Inlet", "Intake", 24.4, "/sensor/1", {}),
            TemperatureReading("Chassis_0", "Outlet", "Exhaust", "31.2", "/sensor/2", {}),
            TemperatureReading("Chassis_0", "Bad", "Unknown", None, "/sensor/3", {}),
        ),
        fans=(
            FanReading("Chassis_0", "Fan 1", "Enabled", "OK", 42, "/fan/1", {}),
        ),
        raw={},
    )

    status = module.build_status(system, (), thermal, polled_at=polled_at)

    assert status == {
        "powerState": "On",
        "health": "OK",
        "temperature": {
            "count": 2,
            "maxCelsius": 31.2,
        },
        "lastPolled": "2026-07-10T14:40:00Z",
    }


def test_poll_endpoint_reads_gb300_corpus_without_mutating_requests() -> None:
    """The poll path uses read commands through the facade and never writes."""
    requests_mock = pytest.importorskip("requests_mock")
    module = _load_controller_module()
    seen_methods: list[str] = []

    def get_cb(request, context):
        seen_methods.append(request.method)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text(encoding="utf-8")

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            idrac_port=8080,
            insecure=True,
            is_http=True,
            is_debug=False,
        )
        status = module.poll_endpoint(
            {
                "address": "mock-gb300",
                "port": 8080,
                "insecure": True,
                "pollInterval": "30s",
                "secretRef": {"name": "bmc-login"},
            },
            credentials={"username": "root", "password": "mock"},
            manager_factory=lambda **_: manager,
            polled_at=datetime(2026, 7, 10, 14, 45, 0, tzinfo=timezone.utc),
        )

    assert status["powerState"] == "On"
    assert status["health"] == "OK"
    assert status["temperature"]["count"] == 56
    assert status["temperature"]["maxCelsius"] == 54.1875
    assert status["lastPolled"] == "2026-07-10T14:45:00Z"
    # NIC/DPU firmware is pulled read-only and folded into status: the GB300
    # corpus carries 4 ConnectX-8 NICs + 1 BlueField-3 DPU and their firmware.
    nic_fw = status["networkFirmware"]
    assert nic_fw["adapterCount"] == 5
    assert nic_fw["nicCount"] == 4
    assert nic_fw["dpuCount"] == 1
    assert "40.45.3048" in nic_fw["distinctVersions"]
    assert any(c["id"] == "CX8_0" and c["version"] == "40.45.3048"
               for c in nic_fw["components"])
    assert seen_methods
    assert set(seen_methods) == {"GET"}


def test_kopf_handler_patches_status_only(monkeypatch) -> None:
    """The handler writes status through the kopf patch, returns None, never mutates."""
    module = _load_controller_module()
    calls: list[tuple[dict, dict]] = []
    fixed_now = datetime(2026, 7, 10, 14, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: fixed_now)

    def fake_poll_endpoint(spec, credentials=None, manager_factory=None, polled_at=None):
        calls.append((dict(spec), dict(credentials or {})))
        return {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 1, "maxCelsius": 24.4},
            "lastPolled": "2026-07-10T14:50:00Z",
        }

    monkeypatch.setattr(module, "poll_endpoint", fake_poll_endpoint)

    patch: dict = {}
    result = module.poll_redfish_endpoint(
        spec={"address": "mock-bmc", "secretRef": {"name": "bmc-login"}},
        body={},
        namespace="default",
        name="node-a",
        logger=None,
        patch=patch,
    )

    # Status is applied via the injected patch; the handler returns None so kopf
    # does not persist a result under a status field the structural CRD rejects
    # (the source of the "merge-patching inconsistencies" warning every poll).
    assert result is None
    assert patch == {
        "status": {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 1, "maxCelsius": 24.4},
            "lastPolled": "2026-07-10T14:50:00Z",
            "conditions": [
                {
                    "type": "EndpointReachable",
                    "status": "True",
                    "reason": "PollSucceeded",
                    "lastTransitionTime": "2026-07-10T14:50:00Z",
                }
            ],
            "consecutiveFailures": 0,
            "lastError": None,
            "nextPollAfter": None,
        }
    }
    assert calls == [
        (
            {"address": "mock-bmc", "secretRef": {"name": "bmc-login"}},
            {},
        )
    ]


def test_controller_tracing_setup_is_env_gated(monkeypatch) -> None:
    """Controller OTLP setup runs only when the deployment env flag is true."""
    module = _load_controller_module()
    calls: list[str] = []
    monkeypatch.setattr(
        module.tracing,
        "setup_otlp",
        lambda service_name: calls.append(service_name),
    )

    module.setup_controller_tracing({})
    module.setup_controller_tracing({module.OTLP_TRACES_ENV: "yes"})

    assert calls == ["redfish-controller"]


def test_kopf_handler_wraps_poll_in_controller_span(monkeypatch) -> None:
    """Each endpoint reconcile gets a bounded root span with BMC identity."""
    module = _load_controller_module()
    spans: list[FakeSpan] = []

    @contextlib.contextmanager
    def fake_operation_span(name: str):
        span = FakeSpan(name)
        spans.append(span)
        yield span

    def fake_poll_endpoint(spec, credentials=None, manager_factory=None, polled_at=None):
        return {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 1, "maxCelsius": 24.4},
            "lastPolled": "2026-07-10T14:50:00Z",
        }

    monkeypatch.setattr(module.tracing, "operation_span", fake_operation_span)
    monkeypatch.setattr(module, "poll_endpoint", fake_poll_endpoint)

    patch: dict = {}
    module.poll_redfish_endpoint(
        spec={"address": "https://mock-bmc:8443"},
        body={},
        namespace="default",
        name="node-a",
        logger=None,
        patch=patch,
        force=True,
    )

    assert len(spans) == 1
    assert spans[0].name == "k8s.redfish_endpoint.reconcile"
    assert spans[0].attributes == {
        "server.address": "mock-bmc",
        "k8s.namespace.name": "default",
        "k8s.resource.name": "node-a",
        "k8s.resource.kind": "RedfishEndpoint",
    }


def test_sensor_health_falls_back_when_system_health_absent() -> None:
    """Sensor health gives the status a useful fallback when system health is missing."""
    module = _load_controller_module()
    system = SystemStatus(None, None, "Off", None, None, {})
    sensors = (
        SensorReading("Chassis_0", "Temp", 20, "Cel", "Temperature", "OK", {}),
        SensorReading("Chassis_0", "Fan", 40, "%", "Fan", "Warning", {}),
    )
    thermal = ThermalStatus({}, (), (), {})

    status = module.build_status(
        system,
        sensors,
        thermal,
        polled_at=datetime(2026, 7, 10, 14, 55, tzinfo=timezone.utc),
    )

    assert status["powerState"] == "Off"
    assert status["health"] == "Warning"
    assert status["temperature"] == {"count": 0, "maxCelsius": None}


# ---------------------------------------------------------------------------
# P0 - shared, thread-safe Kubernetes client
#
# kopf runs sync handlers on a ThreadPoolExecutor. The old code loaded the kube
# config and built a CoreV1Api on every handler call, racing on the kubernetes
# client's process-global default Configuration. These tests pin the fix: the
# config is loaded exactly once and one client is shared, even when 50 handler
# threads request it simultaneously.
# ---------------------------------------------------------------------------


def test_kube_client_loads_config_once_under_concurrency(monkeypatch) -> None:
    """50 concurrent callers trigger exactly one config load and one client build."""
    from redfish_ctl import kube_client

    kube_client.reset_client_cache()
    try:
        load_calls: list[int] = []
        build_calls: list[int] = []
        sentinel = object()

        def fake_load() -> None:
            load_calls.append(1)

        def fake_build():
            build_calls.append(1)
            return sentinel

        monkeypatch.setattr(kube_client, "_load_kube_config", fake_load)
        monkeypatch.setattr(kube_client, "_build_core_v1_api", fake_build)

        # A barrier releases all workers at once so they collide on the unbuilt
        # singleton — the exact window the old per-call code raced in.
        workers = 50
        barrier = threading.Barrier(workers)

        def worker():
            barrier.wait()
            return kube_client.get_core_v1_api()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = [f.result() for f in [pool.submit(worker) for _ in range(workers)]]

        assert all(result is sentinel for result in results)
        assert load_calls == [1], "kube config must load exactly once for the process"
        assert build_calls == [1], "exactly one CoreV1Api must be shared"
    finally:
        kube_client.reset_client_cache()


def test_load_secret_credentials_uses_shared_client_and_decodes(monkeypatch) -> None:
    """The handler decodes Secret data through the shared client, no per-call config load."""
    import base64

    from redfish_ctl import kube_client

    module = _load_controller_module()
    kube_client.reset_client_cache()
    try:
        load_calls: list[int] = []

        class FakeSecret:
            data = {
                "username": base64.b64encode(b"bmc-admin").decode("ascii"),
                "password": base64.b64encode(b"s3cr3t").decode("ascii"),
            }

        read_args: list[tuple[str, str]] = []

        class FakeApi:
            def read_namespaced_secret(self, name, namespace):
                read_args.append((name, namespace))
                return FakeSecret()

        def fake_load() -> None:
            load_calls.append(1)

        monkeypatch.setattr(kube_client, "_load_kube_config", fake_load)
        monkeypatch.setattr(kube_client, "_build_core_v1_api", lambda: FakeApi())

        creds_first = module.load_secret_credentials("bmc-ns", {"name": "bmc-login"})
        creds_second = module.load_secret_credentials("bmc-ns", {"name": "bmc-login"})

        assert creds_first == {"username": "bmc-admin", "password": "s3cr3t"}
        assert creds_second == creds_first
        # Two credential reads, but the config was loaded once for the process.
        assert load_calls == [1]
        assert read_args == [("bmc-login", "bmc-ns"), ("bmc-login", "bmc-ns")]
    finally:
        kube_client.reset_client_cache()


def test_load_secret_credentials_degrades_when_client_unavailable(monkeypatch) -> None:
    """Offline (no kubernetes/config), credentials fall back to empty, never raising."""
    from redfish_ctl import kube_client

    module = _load_controller_module()
    kube_client.reset_client_cache()
    try:
        def boom() -> None:
            raise ImportError("No module named 'kubernetes'")

        monkeypatch.setattr(kube_client, "_load_kube_config", boom)

        assert module.load_secret_credentials("bmc-ns", {"name": "bmc-login"}) == {}
        # No secretRef / namespace short-circuits before touching any client.
        assert module.load_secret_credentials(None, {"name": "bmc-login"}) == {}
        assert module.load_secret_credentials("bmc-ns", None) == {}
    finally:
        kube_client.reset_client_cache()


# ---------------------------------------------------------------------------
# P2 - honor per-CR spec.pollInterval (the old timer hard-coded interval=30)
# ---------------------------------------------------------------------------


def test_parse_interval_seconds_handles_units_and_bad_input() -> None:
    """pollInterval strings parse to seconds; junk falls back to the default."""
    module = _load_controller_module()
    assert module.parse_interval_seconds("30s", 99.0) == 30.0
    assert module.parse_interval_seconds("5m", 99.0) == 300.0
    assert module.parse_interval_seconds("1h", 99.0) == 3600.0
    assert module.parse_interval_seconds("45", 99.0) == 45.0  # bare number = seconds
    assert module.parse_interval_seconds(120, 99.0) == 120.0
    # Bad / empty / non-positive values degrade to the base cadence.
    assert module.parse_interval_seconds(None, 99.0) == 99.0
    assert module.parse_interval_seconds("", 99.0) == 99.0
    assert module.parse_interval_seconds("soon", 99.0) == 99.0
    assert module.parse_interval_seconds("0s", 99.0) == 99.0
    assert module.parse_interval_seconds(True, 99.0) == 99.0


def test_base_interval_seconds_reads_env(monkeypatch) -> None:
    """The timer's base cadence reads the chart's env var, defaulting to 30s."""
    module = _load_controller_module()
    # Same env name the Helm chart renders from controller.pollInterval.
    assert module.POLL_INTERVAL_ENV == "REDFISH_CONTROLLER_POLL_INTERVAL"
    monkeypatch.delenv(module.POLL_INTERVAL_ENV, raising=False)
    assert module.base_interval_seconds() == module.DEFAULT_POLL_INTERVAL_SECONDS
    monkeypatch.setenv(module.POLL_INTERVAL_ENV, "15")
    assert module.base_interval_seconds() == 15.0
    # Chart passes a duration string like "45s"; parse it too.
    monkeypatch.setenv(module.POLL_INTERVAL_ENV, "45s")
    assert module.base_interval_seconds() == 45.0
    monkeypatch.setenv(module.POLL_INTERVAL_ENV, "nonsense")
    assert module.base_interval_seconds() == module.DEFAULT_POLL_INTERVAL_SECONDS


def test_poll_due_respects_pollInterval_and_backoff() -> None:
    """poll_due gates on per-CR interval and on the post-failure backoff window."""
    module = _load_controller_module()
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)

    def polled_ago(seconds: int) -> str:
        return module._rfc3339(now - timedelta(seconds=seconds))

    spec = {"address": "bmc", "pollInterval": "60s"}
    # No prior successful poll: always due.
    assert module.poll_due(spec, {}, now) is True
    # 30s since last poll but the CR wants 60s: not due yet.
    assert module.poll_due(spec, {"lastPolled": polled_ago(30)}, now) is False
    # 60s elapsed: due.
    assert module.poll_due(spec, {"lastPolled": polled_ago(60)}, now) is True
    # Backoff window still open overrides an otherwise-due interval.
    backing_off = {
        "lastPolled": polled_ago(300),
        "nextPollAfter": module._rfc3339(now + timedelta(seconds=45)),
    }
    assert module.poll_due(spec, backing_off, now) is False
    # Backoff elapsed: due again.
    expired = {
        "lastPolled": polled_ago(300),
        "nextPollAfter": module._rfc3339(now - timedelta(seconds=1)),
    }
    assert module.poll_due(spec, expired, now) is True


def test_handler_skips_poll_when_not_due(monkeypatch) -> None:
    """A not-due timer fire patches nothing and never touches the BMC."""
    module = _load_controller_module()
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)

    called = []
    monkeypatch.setattr(
        module,
        "poll_endpoint",
        lambda *a, **k: called.append(1) or {},
    )

    patch: dict = {}
    recent = module._rfc3339(now - timedelta(seconds=5))
    result = module.poll_redfish_endpoint(
        spec={"address": "bmc", "pollInterval": "60s"},
        body={"status": {"lastPolled": recent}},
        namespace="default",
        name="node-a",
        patch=patch,
    )

    assert result is None
    assert patch == {}
    assert called == []


def test_handler_force_polls_even_when_not_due(monkeypatch) -> None:
    """force=True (create/update path) polls immediately, ignoring the cadence gate."""
    module = _load_controller_module()
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)

    called = []

    def fake_poll(spec, credentials=None, manager_factory=None, polled_at=None):
        called.append(1)
        return {
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 0, "maxCelsius": None},
            "lastPolled": module._rfc3339(now),
        }

    monkeypatch.setattr(module, "poll_endpoint", fake_poll)

    patch: dict = {}
    recent = module._rfc3339(now - timedelta(seconds=1))
    module.poll_redfish_endpoint(
        spec={"address": "bmc", "pollInterval": "1h"},
        body={"status": {"lastPolled": recent}},
        namespace="default",
        name="node-a",
        patch=patch,
        force=True,
    )

    # Would be skipped without force (1s elapsed vs 1h interval); force overrides.
    assert called == [1]
    assert patch["status"]["powerState"] == "On"
    assert patch["status"]["conditions"][0]["status"] == "True"


# ---------------------------------------------------------------------------
# P3 - BMC error handling: catch, backoff, record an error condition, and keep
#      the last good readings instead of raising forever or freezing silently.
# ---------------------------------------------------------------------------


def test_handler_records_error_condition_and_backoff_on_bmc_failure(monkeypatch) -> None:
    """An unreachable BMC yields an error condition + backoff, no exception raised."""
    module = _load_controller_module()
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)

    def raise_conn(*_a, **_k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(module, "poll_endpoint", raise_conn)

    patch: dict = {}
    result = module.poll_redfish_endpoint(
        spec={"address": "bmc", "pollInterval": "30s"},
        body={"status": {}},
        namespace="default",
        name="node-a",
        patch=patch,
    )

    assert result is None
    status = patch["status"]
    # Error path records reachability + backoff, and never writes the readings
    # keys (so a merge-patch preserves the last good ones).
    assert status["consecutiveFailures"] == 1
    assert status["lastError"] == "connection refused"
    assert status["conditions"][0]["type"] == "EndpointReachable"
    assert status["conditions"][0]["status"] == "False"
    assert status["conditions"][0]["reason"] == "BMCUnreachable"
    assert status["nextPollAfter"] == module._rfc3339(now + timedelta(seconds=30))
    assert "powerState" not in status
    assert "lastPolled" not in status


def test_backoff_grows_with_consecutive_failures() -> None:
    """Backoff doubles per failure from the base cadence, capped."""
    module = _load_controller_module()
    assert module.backoff_seconds(1, 30.0) == 30.0
    assert module.backoff_seconds(2, 30.0) == 60.0
    assert module.backoff_seconds(3, 30.0) == 120.0
    assert module.backoff_seconds(99, 30.0) == module.MAX_BACKOFF_SECONDS


def test_handler_error_increments_prior_failure_count(monkeypatch) -> None:
    """A repeat failure grows the counter and the backoff window."""
    module = _load_controller_module()
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: now)
    monkeypatch.setattr(
        module,
        "poll_endpoint",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("read timed out")),
    )

    patch: dict = {}
    module.poll_redfish_endpoint(
        spec={"address": "bmc", "pollInterval": "30s"},
        body={"status": {"consecutiveFailures": 2, "lastPolled": "2026-07-10T14:00:00Z"}},
        namespace="default",
        name="node-a",
        patch=patch,
    )

    status = patch["status"]
    assert status["consecutiveFailures"] == 3
    assert status["conditions"][0]["reason"] == "Timeout"
    # base 30s * 2**(3-1) = 120s backoff.
    assert status["nextPollAfter"] == module._rfc3339(now + timedelta(seconds=120))
    # lastPolled from the prior success is untouched by the error patch.
    assert "lastPolled" not in status
