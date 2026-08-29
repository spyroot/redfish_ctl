"""Helm contract tests for the namespace-scoped DMTF simulator."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "charts" / "dmtf-sim"
BUNDLE = (
    REPO_ROOT
    / "spec"
    / "dmtf"
    / "redfish"
    / "2026.1"
    / "mockups"
    / "DSP2043_2026.1.zip"
)
BUNDLE_SHA256 = "481990aa1e77a675b5cc919483b4bc2dd8e832f91ba9b0e3f001ee2b2c8ddef7"
TEST_COMMIT = "c" * 40
ANNOTATIONS = {
    "dmtf.redfish.ctl.dev/bundle-release": "2026.1",
    "dmtf.redfish.ctl.dev/bundle-sha256": BUNDLE_SHA256,
    "dmtf.redfish.ctl.dev/profile": "public-rackmount1",
    "dmtf.redfish.ctl.dev/source-commit": TEST_COMMIT,
}


def _helm() -> str:
    """Resolve Helm from the gate toolchain and fail when it is absent."""
    helm = shutil.which("helm")
    if helm is None and Path("/opt/homebrew/bin/helm").is_file():
        helm = "/opt/homebrew/bin/helm"
    assert helm is not None, "helm is required by kubernetes.render"
    return helm


def _helm_command(*extra_args: str, include_tests: bool = False) -> list[str]:
    """Build the deterministic namespace-scoped Helm template command."""
    command = [
        _helm(),
        "template",
        "dmtf-sim",
        str(CHART_DIR),
        "--namespace",
        "dmtf-bmc",
        "--set-string",
        f"provenance.sourceCommit={TEST_COMMIT}",
    ]
    if not include_tests:
        command.append("--skip-tests")
    return [*command, *extra_args]


def _template(
    *extra_args: str,
    include_tests: bool = False,
) -> list[dict[str, Any]]:
    """Render the chart and return its non-empty Kubernetes documents."""
    result = subprocess.run(
        _helm_command(*extra_args, include_tests=include_tests),
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Return the single rendered resource with the requested kind."""
    matches = [doc for doc in docs if doc.get("kind") == kind]
    assert len(matches) == 1, f"expected one {kind}, found {len(matches)}"
    return matches[0]


def _walk_keys(value: Any) -> set[str]:
    """Collect every mapping key from a rendered Kubernetes object tree."""
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_walk_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_walk_keys(child))
        return keys
    return set()


def test_chart_metadata_schema_defaults_and_strict_lint() -> None:
    """Chart metadata and defaults remain pinned and strict Helm lint passes."""
    chart = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    schema = json.loads((CHART_DIR / "values.schema.json").read_text(encoding="utf-8"))

    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "dmtf-sim"
    assert chart["type"] == "application"
    assert chart["appVersion"] == "2026.1"
    assert values["image"]["repository"] == (
        "harbor.rnd.embedings.ai:30443/spyroot/redfish-dmtf-sim"
    )
    assert values["image"]["tag"] == "2026.1"
    assert values["service"] == {"type": "ClusterIP", "port": 80}
    assert values["imagePullSecrets"] == ["harbor-registry-pull"]
    assert values["dmtf"]["bundleRelease"] == "2026.1"
    assert values["dmtf"]["bundleSha256"] == BUNDLE_SHA256
    assert values["dmtf"]["profile"] == "public-rackmount1"
    assert values["provenance"]["sourceCommit"] == ""
    assert schema["additionalProperties"] is False

    result = subprocess.run(
        [
            _helm(),
            "lint",
            "--strict",
            str(CHART_DIR),
            "--set-string",
            f"provenance.sourceCommit={TEST_COMMIT}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_render_requires_exact_source_commit_provenance() -> None:
    """An install cannot render without an exact source commit from CI."""
    result = subprocess.run(
        [
            _helm(),
            "template",
            "dmtf-sim",
            str(CHART_DIR),
            "--namespace",
            "dmtf-bmc",
            "--skip-tests",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "provenance.sourceCommit must be an exact 40-character commit SHA" in (
        result.stdout + result.stderr
    )


def test_profile_schema_matches_bundle_service_roots() -> None:
    """The allowed profile enum is derived from every DSP2043 service root."""
    schema = json.loads((CHART_DIR / "values.schema.json").read_text(encoding="utf-8"))
    declared = set(schema["properties"]["dmtf"]["properties"]["profile"]["enum"])

    with zipfile.ZipFile(BUNDLE) as archive:
        discovered = {
            parts[1]
            for member in archive.namelist()
            if len(parts := PurePosixPath(member).parts) == 3
            and parts[2] == "index.json"
            and parts[1].startswith("public-")
        }

    assert declared == discovered


def test_values_schema_rejects_unknown_profile_and_property() -> None:
    """Helm rejects profiles and values outside the closed chart schema."""
    for invalid_args in (
        ("--set-string", "dmtf.profile=no-such-profile"),
        ("--set", "externalAccess.enabled=true"),
        ("--set-string", "service.type=NodePort"),
        ("--set-string", "service.type=LoadBalancer"),
    ):
        result = subprocess.run(
            _helm_command(*invalid_args),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, result.stdout + result.stderr


def test_default_render_is_namespace_scoped_and_has_no_external_surface() -> None:
    """The release renders only allowed namespaced resources in dmtf-bmc."""
    docs = _template()
    kinds = {doc["kind"] for doc in docs}
    forbidden_kinds = {
        "ClusterRole",
        "ClusterRoleBinding",
        "CustomResourceDefinition",
        "Ingress",
        "LoadBalancer",
        "Namespace",
        "PersistentVolume",
        "PersistentVolumeClaim",
        "StorageClass",
        "ValidatingWebhookConfiguration",
        "MutatingWebhookConfiguration",
    }

    assert kinds == {"Deployment", "NetworkPolicy", "Service", "ServiceAccount"}
    assert kinds.isdisjoint(forbidden_kinds)
    assert all(doc["metadata"].get("namespace") == "dmtf-bmc" for doc in docs)

    service = _by_kind(docs, "Service")
    service_host = (
        f"{service['metadata']['name']}.{service['metadata']['namespace']}"
        ".svc.cluster.local"
    )
    assert service_host == "dmtf-sim.dmtf-bmc.svc.cluster.local"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}
    ]
    assert {
        "nodePort",
        "externalIPs",
        "loadBalancerIP",
        "loadBalancerClass",
    }.isdisjoint(_walk_keys(service))

    forbidden_fields = {
        "hostPath",
        "persistentVolumeClaim",
        "hostNetwork",
        "hostPID",
        "hostIPC",
    }
    assert forbidden_fields.isdisjoint(_walk_keys(docs))


def test_image_tag_digest_and_pull_secret_are_templated() -> None:
    """Repository overrides, digest pinning, tags, and pull Secret names render."""
    digest = "sha256:" + "a" * 64
    digest_docs = _template(
        "--set-string",
        "image.repository=registry.invalid/team/sim",
        "--set-string",
        f"image.digest={digest}",
        "--set-string",
        "imagePullSecrets[0]=custom-pull",
    )
    deployment = _by_kind(digest_docs, "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["containers"][0]["image"] == f"registry.invalid/team/sim@{digest}"
    assert pod_spec["imagePullSecrets"] == [{"name": "custom-pull"}]

    tag_docs = _template(
        "--set-string",
        "image.repository=registry.invalid/team/sim",
        "--set-string",
        "image.tag=verified-tag",
    )
    tag_deployment = _by_kind(tag_docs, "Deployment")
    tag_container = tag_deployment["spec"]["template"]["spec"]["containers"][0]
    assert tag_container["image"] == "registry.invalid/team/sim:verified-tag"


def test_pull_secret_is_name_only_and_no_secret_resource_is_rendered() -> None:
    """The chart references a pull Secret without carrying any Secret payload."""
    docs = _template()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(CHART_DIR.rglob("*"))
        if path.is_file()
    )

    assert not any(doc["kind"] == "Secret" for doc in docs)
    assert "kind: Secret" not in source
    assert "stringData:" not in source
    assert '"data"' not in source
    assert "HARBOR_TOKEN" not in source
    assert "HARBOR_PASSWORD" not in source
    assert "harbor-redfish-ctl-publisher" not in source
    assert "harbor-redfish-ctl-reader" not in source


def test_connection_hook_is_namespaced_and_hardened() -> None:
    """The Helm test hook is separate from installed resources and is hardened."""
    docs = _template(
        "--show-only",
        "templates/tests/test-connection.yaml",
        include_tests=True,
    )
    pod = _by_kind(docs, "Pod")
    pod_spec = pod["spec"]
    container = pod_spec["containers"][0]

    assert pod["metadata"]["namespace"] == "dmtf-bmc"
    assert pod["metadata"]["annotations"]["helm.sh/hook"] == "test"
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["restartPolicy"] == "Never"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_profile_argument_provenance_and_workload_security() -> None:
    """Selected profile, provenance, and the read-only workload posture render."""
    commit = "b" * 40
    docs = _template(
        "--set-string",
        "dmtf.profile=public-telemetry",
        "--set-string",
        f"provenance.sourceCommit={commit}",
    )
    deployment = _by_kind(docs, "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    expected_annotations = {
        **ANNOTATIONS,
        "dmtf.redfish.ctl.dev/profile": "public-telemetry",
        "dmtf.redfish.ctl.dev/source-commit": commit,
    }

    assert deployment["metadata"]["annotations"] == expected_annotations
    assert deployment["spec"]["template"]["metadata"]["annotations"] == expected_annotations
    assert container["args"] == [
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--mockup-dir",
        "/mockups/DSP2043_2026.1/public-telemetry",
    ]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["seccompProfile"] == {
        "type": "RuntimeDefault"
    }
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_network_policy_allows_only_same_namespace_clients() -> None:
    """Default ingress is restricted to simulator traffic from its namespace."""
    policy = _by_kind(_template(), "NetworkPolicy")

    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert "egress" not in policy["spec"]
    assert policy["spec"]["ingress"] == [
        {
            "from": [{"podSelector": {}}],
            "ports": [{"protocol": "TCP", "port": 8080}],
        }
    ]
