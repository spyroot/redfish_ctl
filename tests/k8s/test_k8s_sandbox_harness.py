"""Contracts for the local Kubernetes read-path sandbox harness."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KIND_CONFIG = REPO_ROOT / "k8s" / "sandbox" / "kind-config.yaml"
SMOKE_SCRIPT = REPO_ROOT / "k8s" / "sandbox" / "run-sandbox.sh"
SAMPLE_ENDPOINT = REPO_ROOT / "k8s" / "sandbox" / "redfish-endpoint-sample.yaml"
ILO_SAMPLE_ENDPOINT = REPO_ROOT / "k8s" / "sandbox" / "redfish-endpoint-ilo-sim.yaml"
DMTF_SAMPLE_ENDPOINT = (
    REPO_ROOT / "k8s" / "sandbox" / "redfish-endpoint-dmtf-sim.yaml"
)
ILO_SIM_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "ilo-sim.yaml"
DMTF_SIM_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "dmtf-sim.yaml"
DMTF_CREDENTIALS = REPO_ROOT / "k8s" / "sandbox" / "dmtf-credentials.yaml"
CONTROLLER_DEPLOYMENT = REPO_ROOT / "k8s" / "controller" / "deployment.yaml"
CONTROLLER_RBAC = REPO_ROOT / "k8s" / "controller" / "rbac.yaml"
CONTROLLER_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.controller"
ILO_SIM_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.ilo-sim"
DMTF_SIM_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.dmtf-sim"
MAKEFILE = REPO_ROOT / "Makefile"
SANDBOX_README = REPO_ROOT / "k8s" / "sandbox" / "README.md"
K8S_README = REPO_ROOT / "k8s" / "README.md"
NODE_PROFILE_SAMPLE = REPO_ROOT / "k8s" / "sandbox" / "redfish-node-profile-sample.yaml"
MOCK_BMC_MANIFEST = REPO_ROOT / "k8s" / "sandbox" / "mock-bmc.yaml"


def _yaml_documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _link_required_tool(bin_dir: Path, name: str) -> None:
    target = shutil.which(name)
    assert target is not None, f"{name} is required to execute the shell harness"
    (bin_dir / name).symlink_to(target)


def _copy_sandbox_script_workspace(
    tmp_path: Path,
    *,
    expected_sha: str = "expected-dsp2043-sha",
) -> Path:
    workspace = tmp_path / "workspace"
    script_path = workspace / "k8s" / "sandbox" / "run-sandbox.sh"
    bundle_path = (
        workspace / "spec" / "dmtf" / "redfish" / "2026.1" / "mockups"
        / "DSP2043_2026.1.zip"
    )
    contract_path = workspace / "specs" / "sim" / "dmtf-sim-contract.yaml"

    script_path.parent.mkdir(parents=True)
    shutil.copy2(SMOKE_SCRIPT, script_path)
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text("hydrated DSP2043 bundle\n", encoding="utf-8")
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        f"artifacts:\n  sha256: \"{expected_sha}\"\n",
        encoding="utf-8",
    )
    return script_path


def _run_sandbox_hash_check(
    tmp_path: Path,
    *,
    hash_tool: str | None,
    actual_sha: str = "expected-dsp2043-sha",
    expected_sha: str = "expected-dsp2043-sha",
) -> tuple[subprocess.CompletedProcess[str], str]:
    script_path = _copy_sandbox_script_workspace(
        tmp_path,
        expected_sha=expected_sha,
    )
    bin_dir = tmp_path / "bin"
    journal_path = tmp_path / "calls.log"
    bin_dir.mkdir()

    for tool in ("bash", "dirname", "head", "grep", "awk"):
        _link_required_tool(bin_dir, tool)

    _write_executable(
        bin_dir / "git",
        """#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
if [ "$1" = "check-attr" ]; then
    for arg in "$@"; do
        last_arg="$arg"
    done
    printf '%s: filter: lfs\\n' "$last_arg"
    exit 0
fi
printf 'unexpected git invocation: %s\\n' "$*" >&2
exit 99
""",
    )
    _write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
printf 'fake docker sentinel\\n' >&2
exit 42
""",
    )
    _write_executable(
        bin_dir / "kind",
        """#!/usr/bin/env bash
printf 'kind %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
printf 'kind should not run during hash verification tests\\n' >&2
exit 43
""",
    )
    _write_executable(
        bin_dir / "kubectl",
        """#!/usr/bin/env bash
printf 'kubectl %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
printf 'kubectl should not run during hash verification tests\\n' >&2
exit 44
""",
    )
    if hash_tool == "sha256sum":
        _write_executable(
            bin_dir / "sha256sum",
            """#!/usr/bin/env bash
printf 'sha256sum %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
printf '%s  %s\\n' "$SANDBOX_FAKE_SHA" "$1"
""",
        )
    elif hash_tool == "shasum":
        _write_executable(
            bin_dir / "shasum",
            """#!/usr/bin/env bash
printf 'shasum %s\\n' "$*" >> "$SANDBOX_FAKE_JOURNAL"
if [ "$1" != "-a" ] || [ "$2" != "256" ]; then
    printf 'unexpected shasum arguments: %s\\n' "$*" >&2
    exit 99
fi
printf '%s  %s\\n' "$SANDBOX_FAKE_SHA" "$3"
""",
        )
    else:
        assert hash_tool is None

    env = {
        "PATH": str(bin_dir),
        "SANDBOX_BACKENDS": "dmtf-sim",
        "SANDBOX_FAKE_JOURNAL": str(journal_path),
        "SANDBOX_FAKE_SHA": actual_sha,
    }
    result = subprocess.run(
        [str(script_path)],
        cwd=script_path.parents[2],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    journal = journal_path.read_text(encoding="utf-8") if journal_path.exists() else ""
    return result, journal


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


def test_dmtf_sim_endpoint_points_at_reference_service_with_secret_ref() -> None:
    """The DMTF endpoint references the sandbox credential Secret."""
    endpoint = yaml.safe_load(DMTF_SAMPLE_ENDPOINT.read_text(encoding="utf-8"))

    assert endpoint["apiVersion"] == "redfish.ctl.dev/v1alpha1"
    assert endpoint["kind"] == "RedfishEndpoint"
    assert endpoint["metadata"]["name"] == "dmtf-sim"
    assert endpoint["metadata"]["namespace"] == "redfish-sandbox"
    assert endpoint["spec"] == {
        "address": "http://dmtf-sim.redfish-sandbox.svc.cluster.local",
        "port": 80,
        "insecure": True,
        "pollInterval": "10s",
        "secretRef": {"name": "dmtf-sim-credentials"},
    }
    sample_text = DMTF_SAMPLE_ENDPOINT.read_text(encoding="utf-8")
    assert "password" not in sample_text.lower()
    assert "username" not in sample_text.lower()


def test_dmtf_sim_manifest_provides_public_demo_credentials() -> None:
    """The DMTF-only backend ships public demo credentials."""
    secret = yaml.safe_load(DMTF_CREDENTIALS.read_text(encoding="utf-8"))

    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "dmtf-sim-credentials"
    assert secret["metadata"]["namespace"] == "redfish-sandbox"
    assert secret["stringData"] == {"username": "root", "password": "calvin"}


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


def test_dmtf_sim_manifest_deploys_local_get_only_reference_service() -> None:
    """The sandbox DMTF simulator serves the pinned DSP2043 rackmount profile."""
    docs = _yaml_documents(DMTF_SIM_MANIFEST)
    by_kind = {doc["kind"]: doc for doc in docs}

    deployment = by_kind["Deployment"]
    service = by_kind["Service"]
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert deployment["metadata"]["name"] == "dmtf-sim"
    assert deployment["metadata"]["namespace"] == "redfish-sandbox"
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["image"] == "redfish-ctl-dmtf-sim:local"
    assert container["args"] == [
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--mockup-dir",
        "/mockups/DSP2043_2026.1/public-rackmount1",
    ]
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/redfish/v1/TaskService",
        "port": "http",
    }
    assert container["livenessProbe"]["httpGet"] == {
        "path": "/redfish/v1/",
        "port": "http",
    }
    assert (
        container["readinessProbe"]["httpGet"]["path"]
        != container["livenessProbe"]["httpGet"]["path"]
    )
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert service["metadata"]["name"] == "dmtf-sim"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == 80
    assert service["spec"]["ports"][0]["targetPort"] == "http"


def test_dmtf_sim_image_gates_required_dsp2043_resources() -> None:
    """The image build fails unless ServiceRoot and TaskService are valid JSON."""
    dockerfile = DMTF_SIM_DOCKERFILE.read_text(encoding="utf-8")

    service_root = "/mockups/DSP2043_2026.1/public-rackmount1/index.json"
    task_service = (
        "/mockups/DSP2043_2026.1/public-rackmount1/TaskService/index.json"
    )
    assert "python -m zipfile -e /tmp/dsp2043.zip /mockups/" in dockerfile
    assert f"test -s {service_root}" in dockerfile
    assert f"test -s {task_service}" in dockerfile
    assert dockerfile.count("python -m json.tool") == 2
    assert service_root in dockerfile
    assert task_service in dockerfile


def test_controller_deployment_is_read_only_and_uses_local_image() -> None:
    """Both controllers run in one pod with minimal, namespaced Kubernetes RBAC."""
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
        # Both controllers run in one kopf process: the endpoint controller
        # polls read status, the node-profile controller drives gated writes.
        "/app/k8s/controller/redfish_endpoint_controller.py",
        "/app/k8s/controller/redfish_node_profile_controller.py",
    ]
    assert container["env"] == [
        {
            "name": "REDFISH_CONTROLLER_POLL_INTERVAL",
            "value": "30s",
        },
        {
            "name": "REDFISH_CONTROLLER_OTLP_TRACES",
            "value": "false",
        },
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
    assert '".[otlp]"' in dockerfile
    assert "opentelemetry.sdk.trace import TracerProvider" in dockerfile
    assert "opentelemetry.exporter.otlp.proto.http.trace_exporter" in dockerfile
    assert "opentelemetry.exporter.otlp.proto.http.metric_exporter" in dockerfile
    assert "kopf" in dockerfile
    assert "kubernetes" in dockerfile
    assert "USER redfish" in dockerfile
    assert 'ENTRYPOINT ["kopf"]' in dockerfile
    assert "redfish_endpoint_controller.py" in dockerfile
    assert "REDFISH_PASSWORD" not in dockerfile
    assert ("IDRAC_" + "PASSWORD") not in dockerfile


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
    assert ("IDRAC_" + "PASSWORD") not in dockerfile


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
    assert "SANDBOX_BACKENDS=\"${SANDBOX_BACKENDS:-corpus-mock,dmtf-sim}\"" in script
    assert "valid entries: corpus-mock, dmtf-sim, ilo-sim, all" in script
    assert "KUBECTL_CONTEXT=\"kind-${KIND_CLUSTER_NAME}\"" in script
    assert kubectl_lines == ['kubectl --context "${KUBECTL_CONTEXT}" "$@"']
    assert "has_backend \"corpus-mock\"" in script
    assert "has_backend \"dmtf-sim\"" in script
    assert "assert_dmtf_bundle" in script
    assert "git check-attr filter" in script
    assert "version https://git-lfs.github.com/spec/v1" in script
    assert "sha256sum" in script
    assert "shasum -a 256" in script
    assert "has_backend \"ilo-sim\"" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/dmtf-credentials.yaml" in script
    assert "kind create cluster --name \"${KIND_CLUSTER_NAME}\"" in script
    assert "kind load docker-image redfish-ctl-mock-bmc:local" in script
    assert "kind load docker-image redfish-ctl-dmtf-sim:local" in script
    assert "kind load docker-image redfish-ctl-ilo-sim:local" in script
    assert "kind load docker-image redfish-ctl-controller:local" in script
    assert "kubectl_sandbox apply -f k8s/controller/redfish-endpoint-crd.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/mock-bmc.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/mock-credentials.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/dmtf-sim.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/ilo-sim.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/ilo-credentials.yaml" in script
    assert "kubectl_sandbox apply -f k8s/controller/rbac.yaml" in script
    assert "kubectl_sandbox apply -f k8s/controller/deployment.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-sample.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-dmtf-sim.yaml" in script
    assert "kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-ilo-sim.yaml" in script
    assert "jsonpath={.status.powerState}" in script
    assert ".status.conditions[?(@.type==" in script
    assert "assert_endpoint_condition" in script
    assert "wait_for_endpoint gb300-mock" in script
    assert "wait_for_endpoint dmtf-sim" in script
    assert "assert_dmtf_profile_in_pod" in script
    assert 'exec deploy/dmtf-sim --' in script
    assert '${DMTF_PROFILE_ROOT}/TaskService/index.json' in script
    assert "dmtf-sim ProfileResolved True DmtfProfileSelected" in script
    assert "assert_endpoint_condition dmtf-sim Ready True PollSucceeded" in script
    assert "wait_for_endpoint ilo-sim" in script
    assert "kubectl delete" not in script
    assert "docker push" not in script


def test_sandbox_script_accepts_sha256sum_without_shasum(tmp_path: Path) -> None:
    """Linux-style sha256sum is enough to verify the DSP2043 bundle."""
    result, journal = _run_sandbox_hash_check(tmp_path, hash_tool="sha256sum")

    assert result.returncode == 42
    assert (
        "DSP2043 bundle verified: release=2026.1 "
        "sha256=expected-dsp2043-sha"
    ) in result.stdout
    assert "sha256sum spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip" in journal
    assert "shasum" not in journal
    assert "docker build -f docker/Dockerfile.dmtf-sim" in journal


def test_sandbox_script_falls_back_to_shasum_when_sha256sum_is_absent(
    tmp_path: Path,
) -> None:
    """macOS-style shasum remains supported when sha256sum is unavailable."""
    result, journal = _run_sandbox_hash_check(tmp_path, hash_tool="shasum")

    assert result.returncode == 42
    assert (
        "DSP2043 bundle verified: release=2026.1 "
        "sha256=expected-dsp2043-sha"
    ) in result.stdout
    assert (
        "shasum -a 256 spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip"
    ) in journal
    assert "sha256sum" not in journal
    assert "docker build -f docker/Dockerfile.dmtf-sim" in journal


def test_sandbox_script_fails_when_no_sha256_tool_is_available(
    tmp_path: Path,
) -> None:
    """The bundle gate fails closed when no SHA-256 implementation is available."""
    result, journal = _run_sandbox_hash_check(tmp_path, hash_tool=None)

    assert result.returncode == 1
    assert "DSP2043 verification requires sha256sum or shasum" in result.stderr
    assert "docker " not in journal
    assert "kind " not in journal
    assert "kubectl " not in journal


def test_sandbox_script_rejects_wrong_dsp2043_digest_before_mutation(
    tmp_path: Path,
) -> None:
    """A digest mismatch stops before Docker, kind, or Kubernetes commands run."""
    result, journal = _run_sandbox_hash_check(
        tmp_path,
        hash_tool="sha256sum",
        actual_sha="wrong-dsp2043-sha",
    )

    assert result.returncode == 1
    assert (
        "DSP2043 bundle hash mismatch: expected expected-dsp2043-sha, "
        "got wrong-dsp2043-sha"
    ) in result.stderr
    assert "sha256sum spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip" in journal
    assert "docker " not in journal
    assert "kind " not in journal
    assert "kubectl " not in journal


def test_sandbox_readme_documents_simulator_backend_selection() -> None:
    """Operators can opt into the simulator matrix from the sandbox docs."""
    readme = SANDBOX_README.read_text(encoding="utf-8")

    assert "SANDBOX_BACKENDS=corpus-mock,ilo-sim make k8s-sandbox" in readme
    assert "SANDBOX_BACKENDS=dmtf-sim make k8s-sandbox" in readme
    assert "DMTF DSP2043 simulator" in readme
    assert "ProfileResolved=True/DmtfProfileSelected" in readme
    assert "Ready=True/PollSucceeded" in readme
    assert "HPE iLO Redfish emulator" in readme
    assert "https://github.com/HewlettPackard/ilo-redfish-emulator" in readme


def test_sandbox_docs_show_teardown_and_plain_endpoint_listing() -> None:
    """Sandbox docs include cleanup and rely on CRD default columns."""
    sandbox_readme = SANDBOX_README.read_text(encoding="utf-8")
    k8s_readme = K8S_README.read_text(encoding="utf-8")

    assert "KEEP_CLUSTER=1 make k8s-sandbox" in sandbox_readme
    assert "make k8s-sandbox-down" in sandbox_readme
    assert "$ kubectl get redfishendpoints\n" in k8s_readme
    assert "custom-columns" not in k8s_readme


def test_make_k8s_sandbox_invokes_smoke_harness() -> None:
    """The Makefile target should run the DS4 harness, not a placeholder."""
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "k8s/sandbox/run-sandbox.sh" in makefile
    assert "kind-config.yaml is not present yet" not in makefile


# --- write/CONVERGE leg (RedfishNodeProfile) -----------------------------------


def test_node_profile_sample_arms_pxe_boot_via_secret_ref() -> None:
    """The sample profile drives a gated one-time PXE boot with no inline secrets."""
    profile = yaml.safe_load(NODE_PROFILE_SAMPLE.read_text(encoding="utf-8"))

    assert profile["kind"] == "RedfishNodeProfile"
    assert profile["metadata"]["namespace"] == "redfish-sandbox"
    spec = profile["spec"]
    assert spec["endpoint"]["address"].startswith("http://mock-bmc.redfish-sandbox")
    assert spec["endpoint"]["secretRef"]["name"] == "mock-bmc-credentials"
    assert spec["desiredState"]["boot"]["device"] == "Pxe"
    # Credentials come only through the Secret reference (a key name, never a
    # value); no approval is baked in — an operator sets approvedPlanHash after
    # seeing the plan.
    assert "approvedPlanHash" not in spec
    assert set(spec["endpoint"]["secretRef"]) >= {"name", "usernameKey", "passwordKey"}
    assert "password" not in {k.lower() for k in spec["endpoint"].keys()}


def test_controller_rbac_grants_node_profile_status_but_not_delete() -> None:
    """The controller can watch/patch RedfishNodeProfiles and their status only."""
    role = next(
        doc for doc in _yaml_documents(CONTROLLER_RBAC) if doc["kind"] == "Role"
    )
    resources = {
        resource
        for rule in role["rules"]
        for resource in rule.get("resources", [])
    }
    assert "redfishnodeprofiles" in resources
    assert "redfishnodeprofiles/status" in resources

    profile_verbs = set().union(
        *(
            set(rule["verbs"])
            for rule in role["rules"]
            if "redfishnodeprofiles" in rule.get("resources", [])
        )
    )
    assert {"get", "list", "watch", "patch", "update"} <= profile_verbs
    assert "delete" not in profile_verbs
    assert "create" not in profile_verbs


def test_mock_bmc_runs_with_mounted_mutation_rules() -> None:
    """The mock accepts writes via a ConfigMap-mounted mutation-rules file."""
    deployment = next(
        doc
        for doc in _yaml_documents(MOCK_BMC_MANIFEST)
        if doc["kind"] == "Deployment"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert "--mutation-rules" in container["args"]
    rules_path = container["args"][container["args"].index("--mutation-rules") + 1]
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/rules")
    assert rules_path.startswith("/rules/")
    assert mount["readOnly"] is True
    volume = next(v for v in pod["volumes"] if v["name"] == mount["name"])
    assert volume["configMap"]["name"] == "mock-bmc-mutation-rules"


def test_run_sandbox_drives_the_node_profile_write_leg() -> None:
    """The harness applies the profile CRD/CR and runs the plan->approve->apply flow."""
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "redfish-node-profile-crd.yaml" in script
    assert "redfish-node-profile-sample.yaml" in script
    assert "configmap mock-bmc-mutation-rules" in script
    assert "supermicro_gb300.yaml=tests/mutation_rules/supermicro_gb300.yaml" in script
    # The approval flow: read the plan hash, patch approvedPlanHash, wait applied.
    assert "wait_for_node_profile_plan" in script
    assert "approvedPlanHash" in script
    assert "wait_for_node_profile_applied" in script
    # Convergence: after apply the mock reflects the change, so drift clears.
    assert "wait_for_node_profile_converged" in script
    assert "drive_node_profile gb300-mock" in script
