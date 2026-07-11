"""Offline contract tests for the simulated GB300 fleet (36 mock BMCs).

These validate the fleet manifest and the mock's per-pod identity overlay
without a cluster or a live BMC — the ordinal→rack/slot math, the 36-replica
StatefulSet shape, and that different ordinals produce distinct node
identities from the single committed corpus image.

Author Mus <spyroot@gmail.com>
"""
import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
FLEET_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "gb300-fleet.yaml"
MOCK_SERVER = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"


def _load_mock_module():
    """Import the mock server module directly (it lives outside the package)."""
    spec = importlib.util.spec_from_file_location("mock_bmc_server", MOCK_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docs():
    return [d for d in yaml.safe_load_all(FLEET_MANIFEST.read_text(encoding="utf-8")) if d]


def test_fleet_is_a_36_replica_statefulset_with_a_headless_service():
    """The fleet is one StatefulSet of 36 pods behind a headless Service."""
    by_kind = {d["kind"]: d for d in _docs()}

    service = by_kind["Service"]
    assert service["spec"]["clusterIP"] == "None"      # headless → stable per-pod DNS
    assert service["metadata"]["name"] == "gb300-bmc"

    sts = by_kind["StatefulSet"]
    assert sts["spec"]["replicas"] == 36               # 18 slots x 2 racks
    assert sts["spec"]["serviceName"] == "gb300-bmc"
    pod_spec = sts["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert container["ports"][0]["containerPort"] == 8080
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    # Numeric runAsUser so runAsNonRoot is verifiable at admission (a named
    # image user cannot be checked); must match the image's pinned uid.
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 10001
    # Memory request stays small so 36 pods fit on one kind node.
    assert container["resources"]["requests"]["memory"] == "16Mi"
    # Corpus comes from the image layer, not a per-pod volume copy.
    assert "volumeClaimTemplates" not in sts["spec"]
    assert not pod_spec.get("volumes")


def test_container_derives_rack_and_slot_from_the_ordinal():
    """The startup command injects MOCK_BMC_RACK/SLOT from the pod ordinal."""
    sts = next(d for d in _docs() if d["kind"] == "StatefulSet")
    args = sts["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert "MOCK_BMC_RACK" in args and "MOCK_BMC_SLOT" in args
    assert "ORD" in args and "HOSTNAME" in args


def test_identity_overlay_is_empty_without_env(monkeypatch):
    """A single-node sandbox (no rack/slot env) is unchanged — no overlay."""
    module = _load_mock_module()
    monkeypatch.delenv("MOCK_BMC_RACK", raising=False)
    monkeypatch.delenv("MOCK_BMC_SLOT", raising=False)
    assert module.identity_overlay_from_env() == {}


def test_identity_overlay_gives_distinct_nodes_per_ordinal(monkeypatch):
    """Different rack/slot produce distinct serials/names — 36 nodes, one corpus."""
    module = _load_mock_module()

    monkeypatch.setenv("MOCK_BMC_RACK", "1")
    monkeypatch.setenv("MOCK_BMC_SLOT", "1")
    a = module.identity_overlay_from_env()

    monkeypatch.setenv("MOCK_BMC_RACK", "2")
    monkeypatch.setenv("MOCK_BMC_SLOT", "18")
    b = module.identity_overlay_from_env()

    sys_key = "/redfish/v1/systems/system_0"
    assert a[sys_key]["SerialNumber"] == "GB300-R1-S01"
    assert b[sys_key]["SerialNumber"] == "GB300-R2-S18"
    assert a[sys_key]["SerialNumber"] != b[sys_key]["SerialNumber"]
    # Never overlays structural fields that the link-walk depends on.
    assert "Id" not in a[sys_key] and "@odata.id" not in a[sys_key]
    # The manager resource also gets a distinct identity.
    mgr_key = "/redfish/v1/managers/bmc_0"
    assert a[mgr_key]["Name"] == "GB300-R1-S01-BMC"
