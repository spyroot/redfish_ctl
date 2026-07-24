"""Contracts for the Kubernetes RedfishNodeProfile controller."""

from __future__ import annotations

import contextlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_MODULE = REPO_ROOT / "k8s" / "controller" / "redfish_node_profile_controller.py"
CRD_MANIFEST = REPO_ROOT / "k8s" / "controller" / "redfish-node-profile-crd.yaml"


class FakeStep:
    """Small reconcile step object matching the public attributes the controller reads."""

    def __init__(self, kind: str, required: bool, description: str) -> None:
        self.kind = kind
        self.required = required
        self.description = description
        self.preview = {"kind": kind}


class FakeAppliedChange:
    """Small applied-change object matching the reconciler result contract."""

    def __init__(self, kind: str, changed: bool) -> None:
        self.kind = kind
        self.changed = changed
        self.result = {"kind": kind, "changed": changed}


class FakeResult:
    """Small reconcile result object matching the attributes used by status rendering."""

    def __init__(self, *, dry_run: bool, applied: tuple[FakeAppliedChange, ...] = ()) -> None:
        self.dry_run = dry_run
        self.steps = (
            FakeStep("bios-profile", True, "BIOS profile gb300-power-capped"),
            FakeStep("ntp", False, "Manager NTP servers"),
        )
        self.applied = applied


def _load_controller_module():
    spec = importlib.util.spec_from_file_location(
        "redfish_node_profile_controller",
        CONTROLLER_MODULE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_crd_schema_pins_profile_spec_and_status_shape() -> None:
    """The profile CRD keeps desired state explicit and approval separate."""
    crd = yaml.safe_load(CRD_MANIFEST.read_text(encoding="utf-8"))
    schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    spec_props = schema["properties"]["spec"]["properties"]
    status_props = schema["properties"]["status"]["properties"]

    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["spec"]["names"]["kind"] == "RedfishNodeProfile"
    assert crd["spec"]["scope"] == "Namespaced"
    assert set(spec_props) == {
        "endpoint",
        "desiredState",
        "approve",
        "approvedPlanHash",
        "waitForReboot",
    }
    assert spec_props["approve"]["default"] is False
    assert spec_props["approvedPlanHash"]["type"] == "string"
    endpoint_props = spec_props["endpoint"]["properties"]
    assert set(endpoint_props) == {
        "address",
        "port",
        "insecure",
        "secretRef",
    }
    desired_props = spec_props["desiredState"]["properties"]
    assert set(desired_props) == {
        "biosProfile",
        "ntp",
        "boot",
        "reboot",
    }
    assert set(status_props) == {
        "dryRun",
        "drift",
        "planHash",
        "consumedPlanHash",
        "plannedSteps",
        "appliedChanges",
        "conditions",
        "lastReconciled",
    }


def test_build_status_reports_drift_without_apply_when_unapproved() -> None:
    """Status keeps drift visible while showing that unapproved resources are dry-runs."""
    module = _load_controller_module()
    status = module.build_status(
        FakeResult(dry_run=True),
        approved=False,
        reconciled_at=datetime(2026, 7, 10, 22, 40, tzinfo=timezone.utc),
    )

    assert status["dryRun"] is True
    assert status["drift"] is True
    assert status["planHash"] == module.plan_hash(status["plannedSteps"])
    assert "consumedPlanHash" not in status
    assert status["plannedSteps"][0] == {
        "kind": "bios-profile",
        "required": True,
        "description": "BIOS profile gb300-power-capped",
        "preview": {"kind": "bios-profile"},
    }
    assert status["appliedChanges"] == []
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Approved"]["status"] == "False"
    assert conditions["Approved"]["reason"] == "ApprovalRequired"
    assert conditions["DriftDetected"]["status"] == "True"
    assert conditions["Applied"]["status"] == "False"
    assert conditions["Applied"]["reason"] == "DryRun"
    assert status["lastReconciled"] == "2026-07-10T22:40:00Z"


def test_reconcile_profile_requires_approval_before_confirming() -> None:
    """The controller never applies desired state until spec.approve is true."""
    module = _load_controller_module()
    calls: list[dict] = []

    def fake_reconcile(manager, desired, **kwargs):
        calls.append({"manager": manager, "desired": desired, **kwargs})
        return FakeResult(dry_run=not kwargs["confirm"])

    status = module.reconcile_profile(
        {
            "endpoint": {"address": "mock-bmc", "secretRef": {"name": "bmc-login"}},
            "desiredState": {"biosProfile": "gb300-power-capped"},
        },
        credentials={"username": "root", "password": "mock"},
        manager_factory=lambda **kwargs: kwargs,
        reconcile_func=fake_reconcile,
        reconciled_at=datetime(2026, 7, 10, 22, 45, tzinfo=timezone.utc),
    )

    assert calls == [
        {
            "manager": {
                "idrac_ip": "mock-bmc",
                "idrac_username": "root",
                "idrac_password": "mock",
                "idrac_port": 443,
                "insecure": True,
                "is_http": False,
                "is_debug": False,
            },
            "desired": {"biosProfile": "gb300-power-capped"},
            "confirm": False,
            "wait_for_reboot": False,
            "async_call": False,
        }
    ]
    assert status["dryRun"] is True
    assert {item["type"]: item["status"] for item in status["conditions"]}["Approved"] == "False"


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
    module.setup_controller_tracing({module.OTLP_TRACES_ENV: "true"})

    assert calls == ["redfish-controller"]


def test_kopf_handler_wraps_node_profile_reconcile_in_controller_span(
    monkeypatch,
) -> None:
    """Each node-profile reconcile gets a root span with BMC identity."""
    module = _load_controller_module()
    spans: list[FakeSpan] = []

    @contextlib.contextmanager
    def fake_operation_span(name: str):
        span = FakeSpan(name)
        spans.append(span)
        yield span

    monkeypatch.setattr(module.tracing, "operation_span", fake_operation_span)
    monkeypatch.setattr(module, "load_secret_credentials", lambda namespace, secret_ref: {})
    monkeypatch.setattr(
        module,
        "reconcile_profile",
        lambda *args, **kwargs: {
            "dryRun": True,
            "drift": False,
            "plannedSteps": [],
            "appliedChanges": [],
        },
    )

    patch: dict = {}
    module.reconcile_redfish_node_profile(
        spec={
            "endpoint": {"address": "https://mock-bmc:8443"},
            "desiredState": {"biosProfile": "balanced"},
        },
        body={},
        namespace="default",
        name="profile-a",
        logger=None,
        patch=patch,
    )

    assert patch["status"]["dryRun"] is True
    assert len(spans) == 1
    assert spans[0].name == "k8s.redfish_node_profile.reconcile"
    assert spans[0].attributes == {
        "server.address": "mock-bmc",
        "k8s.namespace.name": "default",
        "k8s.resource.name": "profile-a",
        "k8s.resource.kind": "RedfishNodeProfile",
    }


def test_reconcile_profile_confirms_only_for_matching_plan_hash() -> None:
    """A matching approvedPlanHash applies the current drift plan once."""
    module = _load_controller_module()
    calls: list[dict] = []

    def fake_reconcile(manager, desired, **kwargs):
        calls.append(kwargs)
        if kwargs["confirm"]:
            return FakeResult(
                dry_run=False,
                applied=(FakeAppliedChange("bios-profile", True),),
            )
        return FakeResult(dry_run=True)

    dry_run = FakeResult(dry_run=True)
    approved_hash = module.plan_hash([
        module._planned_step(step) for step in dry_run.steps
    ])

    status = module.reconcile_profile(
        {
            "endpoint": {"address": "https://mock-bmc:8443", "secretRef": {"name": "bmc-login"}},
            "desiredState": {"biosProfile": "gb300-power-capped"},
            "approvedPlanHash": approved_hash,
            "waitForReboot": True,
        },
        credentials={"username": "root", "password": "mock"},
        manager_factory=lambda **kwargs: kwargs,
        reconcile_func=fake_reconcile,
        reconciled_at=datetime(2026, 7, 10, 22, 50, tzinfo=timezone.utc),
    )

    assert calls == [
        {
            "confirm": False,
            "wait_for_reboot": False,
            "async_call": False,
        },
        {
            "confirm": True,
            "wait_for_reboot": True,
            "async_call": False,
        }
    ]
    assert status["dryRun"] is False
    assert status["planHash"] == approved_hash
    assert status["consumedPlanHash"] == approved_hash
    assert status["appliedChanges"] == [
        {
            "kind": "bios-profile",
            "changed": True,
            "result": {"kind": "bios-profile", "changed": True},
        }
    ]
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Approved"]["status"] == "True"
    assert conditions["Applied"]["status"] == "True"


def test_reconcile_profile_does_not_reapply_consumed_plan_hash() -> None:
    """A consumed approvedPlanHash cannot reapply on the next timer tick."""
    module = _load_controller_module()
    calls: list[dict] = []

    def fake_reconcile(manager, desired, **kwargs):
        calls.append(kwargs)
        return FakeResult(dry_run=not kwargs["confirm"])

    dry_run = FakeResult(dry_run=True)
    consumed_hash = module.plan_hash([
        module._planned_step(step) for step in dry_run.steps
    ])

    status = module.reconcile_profile(
        {
            "endpoint": {"address": "mock-bmc"},
            "desiredState": {"biosProfile": "gb300-power-capped"},
            "approvedPlanHash": consumed_hash,
        },
        current_status={"consumedPlanHash": consumed_hash},
        manager_factory=lambda **kwargs: kwargs,
        reconcile_func=fake_reconcile,
        reconciled_at=datetime(2026, 7, 10, 22, 55, tzinfo=timezone.utc),
    )

    assert calls == [
        {
            "confirm": False,
            "wait_for_reboot": False,
            "async_call": False,
        }
    ]
    assert status["dryRun"] is True
    assert status["planHash"] == consumed_hash
    assert status["consumedPlanHash"] == consumed_hash
    assert status["appliedChanges"] == []
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Approved"]["status"] == "False"
    assert conditions["Approved"]["reason"] == "ApprovalConsumed"


def test_kopf_handler_reports_reconciler_load_errors_as_status(monkeypatch) -> None:
    """A missing reconcile backend becomes a status condition instead of an exception."""
    module = _load_controller_module()

    def fake_reconcile_profile(*args, **kwargs):
        raise RuntimeError("redfish_ctl.reconcile is unavailable")

    monkeypatch.setattr(module, "reconcile_profile", fake_reconcile_profile)

    patch: dict = {}
    result = module.reconcile_redfish_node_profile(
        spec={
            "endpoint": {"address": "mock-bmc"},
            "desiredState": {"biosProfile": "gb300-power-capped"},
        },
        body={},
        namespace="default",
        name="node-a",
        logger=None,
        patch=patch,
    )

    # Status is applied via the injected patch; the handler returns None so kopf
    # does not persist a result under a status field the structural CRD rejects.
    assert result is None
    assert patch["status"]["dryRun"] is True
    assert patch["status"]["drift"] is None
    conditions = {item["type"]: item for item in patch["status"]["conditions"]}
    assert conditions["ReconcileAvailable"]["status"] == "False"
    assert conditions["ReconcileAvailable"]["reason"] == "BackendUnavailable"
    assert "unavailable" in conditions["ReconcileAvailable"]["message"]
