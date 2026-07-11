"""Guard: the Helm chart CRDs must not drift from the controller CRDs.

The controller manifests in ``k8s/controller/*-crd.yaml`` are the single source
of truth. The Helm chart ships its own copies under
``charts/redfish-controller/crds/``; if they drift, a ``helm install`` provisions
a different API contract than the raw sandbox does. That already bit the
node-profile CRD once: the chart copy omitted ``spec.approvedPlanHash`` and
``status.planHash``/``status.consumedPlanHash`` while keeping a structural
schema, so the API server would have rejected the operator's approval field and
pruned the status fields the controller depends on — silently breaking one-shot
approval on any chart-based deploy.

These tests assert the functional API surface (group, scope, names,
subresources, the openAPIV3 schema, and printer columns) is identical between
each controller CRD and its chart copy, so drift fails CI instead of a cluster.

Author Mus <spyroot@gmail.com>
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]

CRD_PAIRS = [
    (
        REPO_ROOT / "k8s" / "controller" / "redfish-endpoint-crd.yaml",
        REPO_ROOT / "charts" / "redfish-controller" / "crds" / "redfish-endpoint-crd.yaml",
    ),
    (
        REPO_ROOT / "k8s" / "controller" / "redfish-node-profile-crd.yaml",
        REPO_ROOT / "charts" / "redfish-controller" / "crds" / "redfish-node-profile-crd.yaml",
    ),
]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _api_surface(crd: dict) -> dict:
    """The parts of a CRD that define its runtime API contract."""
    spec = crd["spec"]
    return {
        "group": spec["group"],
        "scope": spec["scope"],
        "names": spec["names"],
        "versions": [
            {
                "name": v["name"],
                "served": v.get("served"),
                "storage": v.get("storage"),
                "subresources": v.get("subresources"),
                "schema": v["schema"],
                "printerColumns": v.get("additionalPrinterColumns", []),
            }
            for v in spec["versions"]
        ],
    }


@pytest.mark.parametrize(
    "controller_crd, chart_crd",
    CRD_PAIRS,
    ids=[p[0].stem for p in CRD_PAIRS],
)
def test_chart_crd_matches_controller_crd(controller_crd: Path, chart_crd: Path) -> None:
    """Each chart CRD exposes the exact API contract of the controller CRD."""
    controller = _api_surface(_load(controller_crd))
    chart = _api_surface(_load(chart_crd))
    assert chart == controller, (
        f"{chart_crd.relative_to(REPO_ROOT)} drifted from "
        f"{controller_crd.relative_to(REPO_ROOT)} — regenerate the chart CRD from "
        f"the controller CRD (single source of truth)."
    )


def test_node_profile_chart_crd_keeps_the_approval_fields() -> None:
    """Regression: the chart node-profile CRD must carry the plan-hash approval fields."""
    chart = _load(CRD_PAIRS[1][1])
    schema = chart["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]
    assert "approvedPlanHash" in schema["spec"]["properties"], (
        "spec.approvedPlanHash missing — a structural schema would reject the "
        "operator's approval field, breaking gated mutations on helm install."
    )
    status_props = schema["status"]["properties"]
    assert "planHash" in status_props and "consumedPlanHash" in status_props, (
        "status.planHash/consumedPlanHash missing — the controller reads "
        "consumedPlanHash to avoid re-applying; pruning it breaks one-shot approval."
    )
