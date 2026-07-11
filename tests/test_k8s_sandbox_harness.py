"""Contracts for the local Kubernetes read-path sandbox harness."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
KIND_CONFIG = REPO_ROOT / "k8s" / "sandbox" / "kind-config.yaml"
SMOKE_SCRIPT = REPO_ROOT / "k8s" / "sandbox" / "run-sandbox.sh"
SAMPLE_ENDPOINT = REPO_ROOT / "k8s" / "sandbox" / "redfish-endpoint-sample.yaml"
ILO_SAMPLE_ENDPOINT = REPO_ROOT / "k8s" / "sandbox" / "redfish-endpoint-ilo-sim.yaml"
ILO_SIM_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "ilo-sim.yaml"
CONTROLLER_DEPLOYMENT = REPO_ROOT / "k8s" / "controller" / "deployment.yaml"
CONTROLLER_RBAC = REPO_ROOT / "k8s" / "controller" / "rbac.yaml"
CONTROLLER_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.controller"
ILO_SIM_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.ilo-sim"
MAKEFILE = REPO_ROOT / "Makefile"
SANDBOX_README = REPO_ROOT / "k8s" / "sandbox" / "README.md"
K8S_README = REPO_ROOT / "k8s" / "README.md"


def _yaml_documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def test_kind_config_defines_a_local_redfish_sandbox_cluster() -> None:
    """The kind config keeps the sandbox local and single-node."""
    config = yaml.safe_load(KIND_CONFIG.read_text(encoding="utf-8"))

    assert config["kind"] == "Cluster"
    assert config["apiVersion"] == "kind.x-k8s.io/v1alpha4"
    assert config["nodes"] == [{"role": "control-plane"}]


def test_sample_endpoint_points_at_mock_bmc_without_credentials() -> None:
    """The sample CR reads the mock BMC via a secretRef, never inline secrets.

    The referenced Secret carries only the canonical public Redfish demo
    credentials (the mock ignores authentication); the point is that the
    controller's secretRef -> Secret -> credentials path runs end-to-end.
    """
    endpoint = yaml.safe_load(SAMPLE_ENDPOINT.read_text(encoding="utf-8"))

    assert endpoint["apiVersion"] == "redfish.ctl.dev/v1alpha1"
    assert endpoint["kind"] == "RedfishEndpoint"
    assert endpoint["metadata"]["name"] == "gb300-mock"
    assert endpoint["metadata"]["namespace"] == "redfish-sandbox"
    assert endpoint["spec"] == {
        "address": "http://mock-bmc.redfish-sandbox.svc.cluster.local",
        "port": 80,
        "insecure": True,
        "pollInterval": "10s",
        "secretRef": {"name": "mock-bmc-credentials"},
    }
    # Credentials live only in the Secret manifest, never inline in the CR.
    sample_text = SAMPLE_ENDPOINT.read_text(encoding="utf-8")
    assert "password" not in sample_text.lower().replace("secretref", "")

    secret = yaml.safe_load(
        (SAMPLE_ENDPOINT.parent / "mock-credentials.yaml").read_text(encoding="utf-8")
    )
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "mock-bmc-credentials"
    assert secret["metadata"]["namespace"] == "redfish-sandbox"
    # Public demo credentials only — a real value here would be a leak.
    assert secret["stringData"] == {"username": "root", "password": "calvin"}


def test_ilo_sim_endpoint_points_at_hpe_emulator_secret_ref() -> None:
    """The alternate sample CR drives the controller against the iLO emulator."""
    endpoint = yaml.safe_load(ILO_SAMPLE_ENDPOINT.read_text(encoding="utf-8"))

    assert endpoint["apiVersion"] == "redfish.ctl.dev/v1alpha1"
    assert endpoint["kind"] == "RedfishEndpoint"
    assert endpoint["metadata"]["name"] == "ilo-sim"
    assert endpoint["metadata"]["namespace"] == "redfish-sandbox"
    assert endpoint["spec"] == {
        "address": "https://ilo-sim.redfish-sandbox.svc.cluster.local",
        "port": 443,
        "insecure": True,
        "pollInterval": "10s",
        "secretRef": {"name": "ilo-sim-credentials"},
    }
    sample_text = ILO_SAMPLE_ENDPOINT.read_text(encoding="utf-8")
    assert "root_password" not in sample_text

    secret = yaml.safe_load(
        (ILO_SAMPLE_ENDPOINT.parent / "ilo-credentials.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "ilo-sim-credentials"
    assert secret["metadata"]["namespace"] == "redfish-sandbox"
    assert secret["stringData"] == {"username": "root", "password": "root_password"}


def test_ilo_sim_manifest_deploys_hpe_emulator_service() -> None:
    """The sandbox can run a real HPE iLO Redfish emulator backend."""
    docs = _yaml_documents(ILO_SIM_MANIFEST)
    by_kind = {doc["kind"]: doc for doc in docs}

    deployment = by_kind["Deployment"]
    service = by_kind["Service"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert deployment["metadata"]["name"] == "ilo-sim"
    assert service["metadata"]["name"] == "ilo-sim"
    assert container["image"] == "redfish-ctl-ilo-sim:local"
    assert container["env"] == [
        {"name": "MOCKUP_FOLDER", "value": "DL380a"},
        {"name": "ASYNC_SLEEP", "value": "0"},
        {"name": "PORT", "value": "8443"},
    ]
    assert container["ports"][0]["containerPort"] == 8443
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/redfish/v1/",
        "port": "https",
        "scheme": "HTTPS",
    }
    assert service["spec"]["ports"][0]["port"] == 443
    assert service["spec"]["ports"][0]["targetPort"] == "https"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False


def test_controller_deployment_is_read_only_and_uses_local_image() -> None:
    """The sandbox controller can patch status but has no BMC write path."""
    docs = _yaml_documents(CONTROLLER_RBAC)
    deployment = yaml.safe_load(CONTROLLER_DEPLOYMENT.read_text(encoding="utf-8"))
    role = next(doc for doc in docs if doc["kind"] == "Role")
    service_account = next(doc for doc in docs if doc["kind"] == "ServiceAccount")
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert service_account["metadata"]["name"] == "redfish-endpoint-controller"
    assert deployment["metadata"]["namespace"] == "redfish-sandbox"
    assert container["image"] == "redfish-ctl-controller:local"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
    assert container["args"] == [
        "run",
        "--standalone",
        # The watch stays scoped to the sandbox namespace so the namespaced
        # Role is sufficient for the resource watch itself.
        "--namespace=redfish-sandbox",
        "/app/k8s/controller/redfish_endpoint_controller.py",
    ]

    cluster_role = next(doc for doc in docs if doc["kind"] == "ClusterRole")
    cluster_verbs = set().union(
        *(set(rule["verbs"]) for rule in cluster_role["rules"])
    )
    # kopf's startup observation is read-only; cluster-scope writes would be
    # a regression.
    assert cluster_verbs <= {"get", "list", "watch"}

    redfish_rules = [
        rule
        for rule in role["rules"]
        if "redfish.ctl.dev" in rule.get("apiGroups", [])
    ]
    assert redfish_rules
    allowed_verbs = set().union(*(set(rule["verbs"]) for rule in redfish_rules))
    assert {"get", "list", "watch", "patch", "update"} <= allowed_verbs
    assert "delete" not in allowed_verbs
    assert "create" not in allowed_verbs

    secret_rules = [
        rule
        for rule in role["rules"]
        if "" in rule.get("apiGroups", []) and "secrets" in rule.get("resources", [])
    ]
    assert secret_rules == [
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "verbs": ["get"],
        }
    ]


def test_controller_image_runs_kopf_without_credentials() -> None:
    """The controller image installs runtime deps and starts the Kopf module."""
    dockerfile = CONTROLLER_DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "redfish_ctl" in dockerfile
    assert "kopf" in dockerfile
    assert "kubernetes" in dockerfile
    assert "USER redfish" in dockerfile
    assert 'ENTRYPOINT ["kopf"]' in dockerfile
    assert "redfish_endpoint_controller.py" in dockerfile
    assert "REDFISH_PASSWORD" not in dockerfile
    assert "IDRAC_PASSWORD" not in dockerfile


def test_ilo_sim_image_builds_public_hpe_emulator_without_credentials() -> None:
    """The iLO simulator image is built from the public emulator source."""
    dockerfile = ILO_SIM_DOCKERFILE.read_text(encoding="utf-8")

    assert "https://github.com/HewlettPackard/ilo-redfish-emulator.git" in dockerfile
    assert "ARG ILO_EMULATOR_REF=v1.1.0" in dockerfile
    assert "MOCKUP_FOLDER=DL380a" in dockerfile
    assert "PORT=8443" in dockerfile
    assert "EXPOSE 8443" in dockerfile
    assert "USER ilosim" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "REDFISH_PASSWORD" not in dockerfile
    assert "IDRAC_PASSWORD" not in dockerfile


def test_sandbox_smoke_script_applies_manifests_and_waits_for_status() -> None:
    """The opt-in smoke harness proves the CR status is populated."""
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    mode = os.stat(SMOKE_SCRIPT).st_mode
    kubectl_lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith("kubectl ")
    ]

    assert mode & stat.S_IXUSR
    assert "SANDBOX_BACKENDS=\"${SANDBOX_BACKENDS:-corpus-mock}\"" in script
    assert "KUBECTL_CONTEXT=\"kind-${KIND_CLUSTER_NAME}\"" in script
    assert kubectl_lines == ['kubectl --context "${KUBECTL_CONTEXT}" "$@"']
    assert "has_backend \"corpus-mock\"" in script
    assert "has_backend \"ilo-sim\"" in script
    assert "kind create cluster --name \"${KIND_CLUSTER_NAME}\"" in script
    assert "kind load docker-image redfish-ctl-mock-bmc:local" in script
    assert "kind load docker-image redfish-ctl-ilo-sim:local" in script
    assert "kind load docker-image redfish-ctl-controller:local" in script
    assert "kubectl_sandbox apply -f k8s/controller/redfish-endpoint-crd.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/mock-bmc.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/mock-credentials.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/ilo-sim.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/ilo-credentials.yaml" in script
    assert "kubectl_sandbox apply -f k8s/controller/rbac.yaml" in script
    assert "kubectl_sandbox apply -f k8s/controller/deployment.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-sample.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-ilo-sim.yaml" in script
    assert "jsonpath={.status.powerState}" in script
    assert "wait_for_endpoint gb300-mock" in script
    assert "wait_for_endpoint ilo-sim" in script
    assert "kubectl delete" not in script
    assert "docker push" not in script


def test_sandbox_readme_documents_ilo_backend_selection() -> None:
    """Operators can opt into the simulator matrix from the sandbox docs."""
    readme = SANDBOX_README.read_text(encoding="utf-8")

    assert "SANDBOX_BACKENDS=corpus-mock,ilo-sim make k8s-sandbox" in readme
    assert "HPE iLO Redfish emulator" in readme
    assert "https://github.com/HewlettPackard/ilo-redfish-emulator" in readme


def test_sandbox_docs_show_teardown_and_plain_endpoint_listing() -> None:
    """Sandbox docs include cleanup and rely on CRD default columns."""
    sandbox_readme = SANDBOX_README.read_text(encoding="utf-8")
    k8s_readme = K8S_README.read_text(encoding="utf-8")

    assert "kind delete cluster --name redfish-sandbox" in sandbox_readme
    assert "$ kubectl get redfishendpoints\n" in k8s_readme
    assert "custom-columns" not in k8s_readme


def test_make_k8s_sandbox_invokes_smoke_harness() -> None:
    """The Makefile target should run the DS4 harness, not a placeholder."""
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "k8s/sandbox/run-sandbox.sh" in makefile
    assert "kind-config.yaml is not present yet" not in makefile
