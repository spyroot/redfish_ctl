"""Contracts for the Redfish controller Helm chart."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = REPO_ROOT / "charts" / "redfish-controller"
DEPLOY_SCRIPT = REPO_ROOT / "k8s" / "controller" / "deploy.sh"


def _chart_file(relative_path: str) -> Path:
    return CHART_DIR / relative_path


def _yaml_documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _template(extra_args: list[str] | None = None) -> list[dict]:
    helm = shutil.which("helm")
    if helm is None and Path("/opt/homebrew/bin/helm").exists():
        helm = "/opt/homebrew/bin/helm"
    if helm is None:
        pytest.skip("helm binary is not installed")

    cmd = [
        helm,
        "template",
        "redfish-controller",
        str(CHART_DIR),
        "--namespace",
        "redfish-system",
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _by_kind_name(docs: list[dict], kind: str, name: str) -> dict:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"missing {kind}/{name}")


def test_chart_metadata_and_default_values_are_pinned() -> None:
    chart = yaml.safe_load(_chart_file("Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load(_chart_file("values.yaml").read_text(encoding="utf-8"))

    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "redfish-controller"
    assert chart["type"] == "application"
    assert values["image"]["repository"] == "spyroot/redfish-ctl-controller"
    assert values["mockBmc"]["image"]["repository"] == "spyroot/redfish-ctl-mock-bmc"
    assert values["mockBmc"]["enabled"] is False
    assert values["controller"]["pollInterval"] == "30s"
    assert values["rbac"]["create"] is True
    assert values["serviceAccount"]["create"] is True


def test_crds_are_installed_from_crds_directory() -> None:
    crds = sorted(path.name for path in (CHART_DIR / "crds").glob("*.yaml"))
    endpoint_crd = yaml.safe_load(
        _chart_file("crds/redfish-endpoint-crd.yaml").read_text(encoding="utf-8")
    )
    profile_crd = yaml.safe_load(
        _chart_file("crds/redfish-node-profile-crd.yaml").read_text(encoding="utf-8")
    )

    assert crds == [
        "redfish-endpoint-crd.yaml",
        "redfish-node-profile-crd.yaml",
    ]
    assert endpoint_crd["kind"] == "CustomResourceDefinition"
    assert endpoint_crd["metadata"]["name"] == "redfishendpoints.redfish.ctl.dev"
    assert profile_crd["kind"] == "CustomResourceDefinition"
    assert profile_crd["metadata"]["name"] == "redfishnodeprofiles.redfish.ctl.dev"


def test_default_template_renders_controller_rbac_and_no_mock_bmc() -> None:
    docs = _template()
    deployment = _by_kind_name(docs, "Deployment", "redfish-controller")
    service_account = _by_kind_name(docs, "ServiceAccount", "redfish-controller")
    cluster_role = _by_kind_name(docs, "ClusterRole", "redfish-controller")
    cluster_role_binding = _by_kind_name(docs, "ClusterRoleBinding", "redfish-controller")
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert service_account["metadata"]["namespace"] == "redfish-system"
    assert cluster_role_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "redfish-controller",
            "namespace": "redfish-system",
        }
    ]
    assert container["image"] == "spyroot/redfish-ctl-controller:v1.2.0"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["args"] == [
        "run",
        "--standalone",
        "/app/k8s/controller/redfish_endpoint_controller.py",
        "/app/k8s/controller/redfish_node_profile_controller.py",
    ]
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "redfish-controller"
    assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert cluster_role["rules"]
    assert all(doc.get("metadata", {}).get("name") != "redfish-controller-mock-bmc" for doc in docs)


def test_rbac_can_be_disabled() -> None:
    docs = _template(["--set", "rbac.create=false"])

    assert not any(doc.get("kind") == "ClusterRole" for doc in docs)
    assert not any(doc.get("kind") == "ClusterRoleBinding" for doc in docs)
    _by_kind_name(docs, "Deployment", "redfish-controller")
    _by_kind_name(docs, "ServiceAccount", "redfish-controller")


def test_existing_service_account_name_can_be_used() -> None:
    docs = _template(
        [
            "--set",
            "serviceAccount.create=false",
            "--set",
            "serviceAccount.name=existing-redfish",
        ]
    )
    deployment = _by_kind_name(docs, "Deployment", "redfish-controller")

    assert not any(doc.get("kind") == "ServiceAccount" for doc in docs)
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "existing-redfish"


def test_mock_bmc_can_be_enabled() -> None:
    docs = _template(["--set", "mockBmc.enabled=true"])
    mock_deployment = _by_kind_name(docs, "Deployment", "redfish-controller-mock-bmc")
    mock_service = _by_kind_name(docs, "Service", "redfish-controller-mock-bmc")
    container = mock_deployment["spec"]["template"]["spec"]["containers"][0]

    assert container["image"] == "spyroot/redfish-ctl-mock-bmc:v1.2.0"
    assert container["env"] == [
        {
            "name": "MOCK_BMC_CORPUS_DIR",
            "value": "/corpus/172.25.230.37",
        }
    ]
    assert mock_service["spec"]["ports"] == [{"name": "http", "port": 80, "targetPort": "http"}]


def test_notes_document_secret_ref_convention() -> None:
    notes = _chart_file("templates/NOTES.txt").read_text(encoding="utf-8")

    assert "same namespace" in notes
    assert "username" in notes
    assert "password" in notes
    assert "kubectl create secret generic" in notes


def test_plain_deploy_script_applies_crds_rbac_and_deployment() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'KUBECTL="${KUBECTL:-kubectl}"' in script
    assert "apply -f k8s/controller/redfish-endpoint-crd.yaml" in script
    assert "apply -f k8s/controller/redfish-node-profile-crd.yaml" in script
    assert "apply -f k8s/controller/rbac.yaml" in script
    assert "apply -f k8s/controller/deployment.yaml" in script
    assert "http://" not in script
    assert "PASSWORD" not in script
