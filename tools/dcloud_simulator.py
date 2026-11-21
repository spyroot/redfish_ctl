#!/usr/bin/env python3
"""Publish and verify the protected DCloud DMTF simulator lifecycle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.parse import urlencode

import yaml

REPORTS = Path("reports/simulator")
CHART = Path("charts/dmtf-sim")
DOCKERFILE = Path("docker/Dockerfile.dmtf-sim")
SIMULATOR = Path("k8s/sandbox/mock_bmc_server.py")
CONTRACT = Path("specs/sim/dmtf-sim-contract.yaml")
SOURCE_LABEL = "org.opencontainers.image.revision"
SOURCE_ANNOTATION = "dmtf.redfish.ctl.dev/source-commit"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SAFE_HARBOR = re.compile(r"[a-z0-9.-]+/[a-z0-9][a-z0-9_-]*")
OCI_MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
MUTATIONS = {"publish-image", "publish-chart", "apply", "rollback"}


class LifecycleError(ValueError):
    """A missing prerequisite or inconsistent lifecycle observation."""


def _required(env: dict[str, str], key: str, pattern: re.Pattern | None = None) -> str:
    """Read and validate a named configuration input without logging its value."""
    value = env.get(key, "")
    if not value or (pattern is not None and not pattern.fullmatch(value)):
        raise LifecycleError(f"{key} is missing or invalid")
    return value


@dataclass(frozen=True)
class Target:
    """The exact candidate and project-provided deployment coordinates."""

    commit: str
    pipeline: str
    job: str
    build_namespace: str
    namespace: str
    release: str
    harbor: str

    @classmethod
    def from_environment(cls, env: dict[str, str]) -> Target:
        """Resolve a protected job and its declared route before any live call."""
        source = _required(env, "CI_PIPELINE_SOURCE")
        if (
            env.get("CI_COMMIT_REF_PROTECTED") != "true"
            or env.get("CI_COMMIT_BRANCH") != _required(env, "CI_DEFAULT_BRANCH")
            or source == "merge_request_event"
            or env.get("CI_SERVER_HOST") != _required(env, "REDFISH_CI_SERVER_HOST")
        ):
            raise LifecycleError("the simulator requires the configured protected default branch")
        if (
            not env.get("KUBERNETES_SERVICE_HOST")
            or not Path("/var/run/secrets/kubernetes.io/serviceaccount/token").is_file()
            or env.get("KUBECONFIG")
        ):
            raise LifecycleError("use the protected runner's in-cluster service identity")

        digits = re.compile(r"[1-9][0-9]{0,19}")
        target = cls(
            commit=_required(env, "CI_COMMIT_SHA", SHA),
            pipeline=_required(env, "CI_PIPELINE_ID", digits),
            job=_required(env, "CI_JOB_ID", digits),
            build_namespace=_required(env, "REDFISH_BUILD_NAMESPACE", DNS_LABEL),
            namespace=_required(env, "REDFISH_SIM_NAMESPACE", DNS_LABEL),
            release=_required(env, "PROJECT_SERVICE_NAME", DNS_LABEL),
            harbor=_required(env, "REDFISH_HARBOR_PROJECT", SAFE_HARBOR),
        )
        if target.namespace == target.build_namespace or len(target.release) > 53:
            raise LifecycleError("the simulator needs a dedicated namespace and valid Helm release")
        if run("git", "rev-parse", "HEAD") != target.commit:
            raise LifecycleError("checked-out commit differs from the protected job")
        return target

    @property
    def image_repo(self) -> str:
        """Return the commit-tagged image's repository."""
        return f"{self.harbor}/{CHART.name}"

    @property
    def chart_repo(self) -> str:
        """Return the complete OCI chart repository, distinct from the image."""
        return f"{self.harbor}/charts/{CHART.name}"

    def identity(self) -> dict:
        """Return the stable pipeline identity shared by all stage receipts."""
        return {"commit": self.commit, "pipeline": self.pipeline}


def run(
    *args: str,
    timeout: int = 40,
    data: bytes | None = None,
    extra_env: dict[str, str] | None = None,
    strip_output: bool = True,
) -> str:
    """Run a bounded tool and keep raw responses, arguments, and errors private."""
    command_env = {
        key: value for key, value in os.environ.items()
        if key not in ("REDFISH_HARBOR_USERNAME", "REDFISH_HARBOR_PASSWORD")
    }
    command_env.update(extra_env or {})
    try:
        result = subprocess.run(
            args, input=data, capture_output=True, env=command_env, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleError(f"{args[0]} could not complete within its execution boundary") from exc
    if result.returncode:
        raise LifecycleError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed ({result.returncode})")
    output = result.stdout.decode()
    return output.strip() if strip_output else output


def _read_json(raw: str | bytes) -> dict:
    """Decode a tool response that must contain a JSON object."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise LifecycleError("expected an object in lifecycle input")
    return value


def _canonical(record: dict) -> bytes:
    """Encode a record independently of JSON serialization order and formatting."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def _fingerprint(record: dict) -> str:
    """Return a fingerprint of a canonical JSON record."""
    return hashlib.sha256(_canonical(record)).hexdigest()


def write_receipt(target: Target, name: str, **fields: object) -> dict:
    """Atomically publish an exact-pipeline receipt for a downstream job."""
    record = {"schema": 1, "stage": name, **target.identity(), "job": target.job, **fields}
    REPORTS.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=REPORTS, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(_canonical(record) + b"\n")
            temporary.flush()
            path.replace(REPORTS / f"{name}.json")
        finally:
            path.unlink(missing_ok=True)
    return record


def load_receipt(target: Target, name: str) -> dict:
    """Reject missing, malformed and cross-pipeline stage receipts."""
    path = REPORTS / f"{name}.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise LifecycleError(f"{name} receipt is not a bounded regular file")
        record = _read_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"{name} receipt is unavailable or invalid") from exc
    expected = {"schema": 1, "stage": name, **target.identity()}
    if any(record.get(key) != value for key, value in expected.items()):
        raise LifecycleError(f"{name} receipt does not match this pipeline and commit")
    return record


class Registry:
    """Read immutable descriptors through the configured Harbor HTTPS origin."""

    def __init__(self, target: Target, env: dict[str, str]):
        self.host = target.harbor.split("/", 1)[0]
        self.username = _required(env, "REDFISH_HARBOR_USERNAME")
        self.password = _required(env, "REDFISH_HARBOR_PASSWORD")

    def _get(
        self, path: str, headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        """Read one bounded response without redirects or logging credentials."""
        conn = HTTPSConnection(self.host, timeout=15)
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            content = response.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise LifecycleError("registry metadata exceeded its size limit")
            return response.status, content, response.getheader("Docker-Content-Digest", "")
        except (OSError, HTTPException) as exc:
            raise LifecycleError("Harbor metadata request failed") from exc
        finally:
            conn.close()

    def _repository_get(
        self, repo: str, suffix: str,
    ) -> tuple[int, bytes, str]:
        """Authenticate and read only the selected repository in this Harbor project."""
        relative = repo.removeprefix(f"{self.host}/")
        if relative == repo:
            raise LifecycleError("artifact repository does not match the Harbor authority")
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        query = urlencode({"service": "harbor-registry", "scope": f"repository:{relative}:pull"})
        status, content, _ = self._get(f"/service/token?{query}", {"Authorization": f"Basic {auth}"})
        if status != 200:
            raise LifecycleError(f"Harbor repository token request failed ({status})")
        token = _read_json(content).get("token")
        if not isinstance(token, str) or not token:
            raise LifecycleError("Harbor returned no scoped registry token")
        return self._get(
            f"/v2/{relative}/{suffix}",
            {"Authorization": f"Bearer {token}", "Accept": OCI_MANIFESTS},
        )

    def manifest(self, repo: str, reference: str) -> tuple[str, dict] | None:
        """Read an OCI manifest and verify its advertised digest against its bytes."""
        status, content, digest = self._repository_get(repo, f"manifests/{reference}")
        if status == 404:
            return None
        if status != 200 or digest != f"sha256:{hashlib.sha256(content).hexdigest()}":
            raise LifecycleError(f"Harbor manifest read-back failed ({status})")
        if DIGEST.fullmatch(reference) and reference != digest:
            raise LifecycleError("Harbor returned a different immutable digest")
        return digest, _read_json(content)

    def image_digest(self, target: Target) -> str | None:
        """Verify image publication and the source commit embedded in its config blob."""
        entry = self.manifest(target.image_repo, target.commit)
        if entry is None:
            return None
        digest, manifest = entry
        config_digest = manifest.get("config", {}).get("digest")
        if not isinstance(config_digest, str) or not DIGEST.fullmatch(config_digest):
            raise LifecycleError("image manifest lacks a valid config digest")
        status, blob, _ = self._repository_get(target.image_repo, f"blobs/{config_digest}")
        if status != 200 or config_digest != f"sha256:{hashlib.sha256(blob).hexdigest()}":
            raise LifecycleError("image config read-back does not match the manifest")
        labels = _read_json(blob).get("config", {}).get("Labels", {})
        if labels.get(SOURCE_LABEL) != target.commit:
            raise LifecycleError("published image is not labeled with this source commit")
        return digest

    def helm(
        self, directory: Path, *args: str, timeout: int = 120,
    ) -> str:
        """Invoke Helm with temporary, isolated registry authentication."""
        config = str(directory / "registry.json")
        if not Path(config).is_file():
            run(
                "helm", "registry", "login", self.host, "--username", self.username,
                "--password-stdin", "--registry-config", config,
                data=f"{self.password}\n".encode(),
            )
        return run("helm", *args, "--registry-config", config, timeout=timeout)


def _bundle(target: Target) -> tuple[Path, str]:
    """Read and validate the tracked DMTF bundle path and expected digest."""
    bundle = yaml.safe_load(run("git", "show", f"{target.commit}:{CONTRACT}"))["binding"]["bundle"]
    path = Path(bundle["artifact"])
    digest = bundle["sha256"]
    if path.is_absolute() or ".." in path.parts or not DIGEST.fullmatch(f"sha256:{digest}"):
        raise LifecycleError("invalid DMTF bundle binding")
    return path, digest


def _build_context(directory: Path, target: Target) -> Path:
    """Archive only exact Docker inputs with the independently hashed LFS bytes."""
    bundle, expected_digest = _bundle(target)
    inputs = directory / "context"
    for name in (DOCKERFILE, SIMULATOR):
        dest = inputs / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw = run("git", "show", f"{target.commit}:{name}", strip_output=False)
        dest.write_bytes(raw.encode())
    dest = inputs / bundle
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with bundle.open("rb") as source, dest.open("wb") as output:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != expected_digest:
        raise LifecycleError("DMTF bundle is unhydrated or disagrees with its tracked SHA-256")
    archive = directory / "simulator.tar"
    with tarfile.open(archive, "w") as tar:
        for name in (DOCKERFILE, SIMULATOR, bundle):
            tar.add(inputs / name, arcname=str(name))
    return archive


def _wait_for_build(namespace: str, name: str) -> str:
    """Read an OpenShift build to a bounded terminal state and validate its digest."""
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        result = _read_json(run("oc", "-n", namespace, "get", name, "-o", "json"))
        status = result.get("status", {})
        phase = status.get("phase")
        if phase == "Complete":
            digest = status.get("output", {}).get("to", {}).get("imageDigest")
            if isinstance(digest, str) and DIGEST.fullmatch(digest):
                return digest
            raise LifecycleError("OpenShift completed the build without an output digest")
        if phase not in ("New", "Pending", "Running"):
            raise LifecycleError("OpenShift simulator build failed")
        time.sleep(5)
    raise LifecycleError("OpenShift simulator build exceeded its deadline")


def _build_image(target: Target, push_secret: str) -> str:
    """Run an isolated binary build and remove only its owned temporary build objects."""
    name = f"sim-{target.pipeline}-{target.job}"
    selector = {"app.kubernetes.io/managed-by": "redfish-ctl-ci", "redfish-ctl/job-id": target.job}
    manifest = {
        "apiVersion": "build.openshift.io/v1",
        "kind": "BuildConfig",
        "metadata": {"name": name, "namespace": target.build_namespace, "labels": selector},
        "spec": {
            "source": {"type": "Binary", "binary": {}},
            "runPolicy": "Serial",
            "serviceAccount": "builder",
            "completionDeadlineSeconds": 600,
            "triggers": [],
            "strategy": {
                "type": "Docker",
                "dockerStrategy": {
                    "dockerfilePath": str(DOCKERFILE),
                    "imageLabels": [{"name": SOURCE_LABEL, "value": target.commit}],
                },
            },
            "output": {
                "to": {"kind": "DockerImage", "name": f"{target.image_repo}:{target.commit}"},
                "pushSecret": {"name": push_secret},
            },
        },
    }
    with tempfile.TemporaryDirectory() as work:
        archive = _build_context(Path(work), target)
        run("oc", "-n", target.build_namespace, "create", "-f", "-", data=_canonical(manifest))
        try:
            build = run(
                "oc", "-n", target.build_namespace, "start-build", name,
                f"--from-archive={archive}", "-o", "name", timeout=180,
            )
            if re.fullmatch(rf"build(?:\.build\.openshift\.io)?/{name}-[1-9][0-9]*", build) is None:
                raise LifecycleError("OpenShift returned an unrelated build identity")
            return _wait_for_build(target.build_namespace, build)
        finally:
            run(
                "oc", "-n", target.build_namespace, "delete", "buildconfig", name,
                "--cascade=foreground", "--wait=true", "--timeout=80s", timeout=100,
            )
            leftover_config = run(
                "oc", "-n", target.build_namespace, "get", "buildconfig", name,
                "--ignore-not-found", "-o", "name",
            )
            leftover_build = run(
                "oc", "-n", target.build_namespace, "get", "build",
                "-l", f"buildconfig={name}", "-o", "name",
            )
            if leftover_config or leftover_build:
                raise LifecycleError("the owned simulator build was not fully removed")


def publish_image(target: Target, env: dict[str, str]) -> dict:
    """Publish a commit-tagged image or reuse the exact independently verified image."""
    registry = Registry(target, env)
    digest = registry.image_digest(target)
    reused = digest is not None
    if digest is None:
        pushed = _build_image(target, _required(env, "REDFISH_HARBOR_BUILD_SECRET", DNS_LABEL))
        digest = registry.image_digest(target)
        if digest != pushed:
            raise LifecycleError("Harbor image digest differs from the completed build")
    return write_receipt(
        target, "image", repository=target.image_repo, digest=digest,
        tag=target.commit, reused=reused, remainingResources=[],
    )


def _chart_version(target: Target) -> str:
    """Derive a Helm-compliant exact-commit version from the tracked chart."""
    version = yaml.safe_load(run("git", "show", f"{target.commit}:{CHART}/Chart.yaml"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise LifecycleError("the simulator chart does not declare a plain SemVer base version")
    return f"{version}-ci.g{target.commit}"


def _archive_members(archive: Path) -> dict[str, str]:
    """Hash Helm tar member contents without extracting untrusted filesystem paths."""
    observed = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or name.parts[:1] != (CHART.name,):
                raise LifecycleError("the simulator chart contains an unexpected archive path")
            if member.isdir():
                continue
            if (
                not member.isfile()
                or member.name in observed
                or len(observed) >= 256
                or member.size > 1024 * 1024
            ):
                raise LifecycleError("the simulator chart archive exceeds its file limits")
            source = tar.extractfile(member)
            if source is None:
                raise LifecycleError("could not read a simulator chart member")
            observed[member.name] = hashlib.sha256(source.read()).hexdigest()
    if not observed:
        raise LifecycleError("the simulator chart archive is empty")
    return observed


def _pull_chart(
    registry: Registry, target: Target, directory: Path, version: str, digest: str,
) -> Path:
    """Download a chart by version and verify the exact plan-bound immutable digest."""
    manifest = registry.manifest(target.chart_repo, digest)
    chart_media_type = "application/vnd.cncf.helm.chart.content.v1.tar+gzip"
    layers = [] if manifest is None else manifest[1].get("layers", [])
    chart_layers = [layer for layer in layers if layer.get("mediaType") == chart_media_type]
    if len(chart_layers) != 1 or not 0 < chart_layers[0].get("size", 0) <= 4 * 1024 * 1024:
        raise LifecycleError("the digest-pinned chart content descriptor is missing or too large")
    registry_config = directory / "auth"
    registry_config.mkdir(exist_ok=True)
    registry.helm(
        registry_config, "pull", f"oci://{target.chart_repo}",
        "--version", version, "--destination", str(directory),
    )
    # Helm reports the digest on stderr. Verify the actual downloaded bytes.
    archive = directory / f"{CHART.name}-{version}.tgz"
    if archive.stat().st_size != chart_layers[0]["size"]:
        raise LifecycleError("the downloaded chart size differs from its immutable OCI descriptor")
    actual_layer = f"sha256:{hashlib.sha256(archive.read_bytes()).hexdigest()}"
    if chart_layers[0].get("digest") != actual_layer:
        raise LifecycleError("the downloaded chart does not match its immutable OCI descriptor")
    return archive


def publish_chart(target: Target, env: dict[str, str]) -> dict:
    """Publish or reuse the commit-versioned Helm package, then verify registry content."""
    registry = Registry(target, env)
    version = _chart_version(target)
    run("git", "diff", "--quiet", target.commit, "--", str(CHART))
    if run("git", "ls-files", "--others", "--", str(CHART)):
        raise LifecycleError("untracked files in the simulator chart would change the package")
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work)
        run("helm", "package", str(CHART), "--version", version, "--destination", work)
        expected = _archive_members(directory / f"{CHART.name}-{version}.tgz")
        remote = registry.manifest(target.chart_repo, version)
        reused = remote is not None
        if remote is None:
            registry.helm(
                directory, "push", str(directory / f"{CHART.name}-{version}.tgz"),
                f"oci://{target.harbor}/charts",
            )
            remote = registry.manifest(target.chart_repo, version)
        if remote is None:
            raise LifecycleError("published chart cannot be read back from Harbor")
        readback = directory / "readback"
        readback.mkdir()
        archive = _pull_chart(registry, target, readback, version, remote[0])
        if _archive_members(archive) != expected:
            raise LifecycleError("published Helm chart files differ from this checkout")
    return write_receipt(
        target, "chart", repository=target.chart_repo, version=version, digest=remote[0],
        reused=reused, remainingResources=[],
    )


def _release_record(target: Target) -> dict | None:
    """Read the active release record, distinguishing absence from command errors."""
    records = json.loads(run(
        "helm", "list", "-n", target.namespace, "--all", "--max", "2",
        "--filter", f"^{target.release}$", "-o", "json",
    ))
    if records == []:
        return None
    if not isinstance(records, list) or len(records) != 1:
        raise LifecycleError("Helm did not return exactly one target release")
    record = records[0]
    if (
        record.get("name") != target.release or record.get("namespace") != target.namespace
        or record.get("status") != "deployed" or not str(record.get("revision", "")).isdigit()
    ):
        raise LifecycleError("the target Helm release is not healthy and deployed")
    return record


def _installed_plan(target: Target) -> str:
    """Read the plan fingerprint from the installed Helm release metadata."""
    release = _read_json(run(
        "helm", "status", target.release, "-n", target.namespace, "-o", "json",
    ))
    return str(release["info"].get("description", ""))


def _kubectl_get(target: Target, kind: str, *args: str) -> dict:
    """Read one resource or list from the exact simulator namespace."""
    return _read_json(run("kubectl", "-n", target.namespace, "get", kind, *args, "-o", "json"))


def _owned_selector(target: Target) -> str:
    """Select only resources owned by the tracked chart and release."""
    return f"app.kubernetes.io/name={CHART.name},app.kubernetes.io/instance={target.release}"


def snapshot(target: Target) -> dict | None:
    """Independently attest Helm identity, the ready rollout, Pod imageIDs and Service."""
    helm = _release_record(target)
    if helm is None:
        remaining = run(
            "kubectl", "-n", target.namespace, "get", "deploy,rs,pod,svc,sa,networkpolicy",
            "-l", _owned_selector(target), "-o", "name",
        )
        if remaining:
            raise LifecycleError("Helm release is absent but its labeled resources remain")
        return None

    deployment = _kubectl_get(target, "deployment", target.release)
    spec = deployment["spec"]
    status = deployment["status"]
    total = spec["replicas"]
    if (
        total < 1
        or status.get("observedGeneration") != deployment["metadata"]["generation"]
        or status.get("readyReplicas") != total
        or status.get("updatedReplicas") != total
        or status.get("replicas") != total
    ):
        raise LifecycleError("simulator Deployment has not converged")
    template = spec["template"]
    commit = template["metadata"]["annotations"][SOURCE_ANNOTATION]
    image = next(item["image"] for item in template["spec"]["containers"] if item["name"] == CHART.name)
    repository, separator, digest = image.partition("@")
    if separator != "@" or repository != target.image_repo or not DIGEST.fullmatch(digest):
        raise LifecycleError("Deployment simulator image is not from the immutable project repository")
    if not SHA.fullmatch(commit):
        raise LifecycleError("Deployment has no exact source commit annotation")
    pods = _kubectl_get(target, "pods", "-l", _owned_selector(target))["items"]
    active = [pod for pod in pods if not pod["metadata"].get("deletionTimestamp")]
    if len(active) != total:
        raise LifecycleError("active simulator Pods do not match the desired replica count")
    for pod in active:
        container = next(
            item for item in pod["status"]["containerStatuses"] if item["name"] == CHART.name
        )
        image_id = container.get("imageID", "")
        observed_digest = image_id.rsplit("@", 1)[-1]
        if (
            not container.get("ready")
            or observed_digest != digest
            or pod["metadata"].get("annotations", {}).get(SOURCE_ANNOTATION) != commit
        ):
            raise LifecycleError("a simulator Pod is not ready on the expected digest and commit")
    service = _kubectl_get(target, "service", target.release)
    port = next(entry["port"] for entry in service["spec"]["ports"] if entry["name"] == "http")
    if (
        service["spec"]["type"] != "ClusterIP"
        or service["spec"]["selector"] != {
            "app.kubernetes.io/name": CHART.name, "app.kubernetes.io/instance": target.release,
        }
        or not isinstance(port, int) or not 0 < port < 65536
    ):
        raise LifecycleError("the simulator Service does not select the verified workload")
    return {
        "revision": int(helm["revision"]), "chart": helm["chart"], "commit": commit,
        "image": image, "readyReplicas": total, "servicePort": port,
    }


def _check_candidate(target: Target, state: dict | None, desired: dict) -> dict:
    """Verify that observed resources exactly identify the selected release."""
    if (
        state is None or state["commit"] != target.commit
        or state["chart"] != f"{CHART.name}-{desired['version']}"
        or state["image"] != desired["image"]
    ):
        raise LifecycleError("the observed simulator does not match the planned release")
    return state


def _candidate(target: Target) -> dict:
    """Verify image and chart receipts and bind them to this project and commit."""
    image = load_receipt(target, "image")
    chart = load_receipt(target, "chart")
    version = _chart_version(target)
    if (
        image.get("repository") != target.image_repo or image.get("tag") != target.commit
        or chart.get("repository") != target.chart_repo or chart.get("version") != version
        or not DIGEST.fullmatch(str(image.get("digest", "")))
        or not DIGEST.fullmatch(str(chart.get("digest", "")))
    ):
        raise LifecycleError("publication receipts disagree with the selected image or chart")
    return {
        "image": f"{target.image_repo}@{image['digest']}", "version": version,
        "chartDigest": chart["digest"],
    }


def _values(target: Target, desired: dict, env: dict[str, str]) -> dict:
    """Compose pinned identity and optionally read exact tracked DCloud Helm overrides."""
    override = env.get("REDFISH_SIM_VALUES_FILE")
    values = {}
    if override:
        path = Path(override)
        if path.is_absolute() or ".." in path.parts:
            raise LifecycleError("simulator values override must be a tracked repository path")
        values = yaml.safe_load(run("git", "show", f"{target.commit}:{path}"))
        if not isinstance(values, dict):
            raise LifecycleError("tracked simulator values override must be a mapping")
    values.update(
        provenance={"sourceCommit": target.commit},
        image={"repository": target.image_repo, "digest": desired["image"].rsplit("@", 1)[-1]},
        imagePullSecrets=[_required(env, "REDFISH_HARBOR_PULL_SECRET", DNS_LABEL)],
    )
    return values


def _render(target: Target, chart: Path, values: Path) -> str:
    """Render the exact proposed Helm release without writing live state."""
    return run(
        "helm", "template", target.release, str(chart),
        "--namespace", target.namespace, "--values", str(values), timeout=120,
    )


def _load_plan(target: Target) -> tuple[dict, dict]:
    """Read a bound, internally consistent plan before any downstream stage."""
    record = load_receipt(target, "plan")
    plan = record.get("plan")
    if not isinstance(plan, dict) or record.get("fingerprint") != _fingerprint(plan):
        raise LifecycleError("simulator plan fingerprint is invalid")
    if (plan.get("namespace"), plan.get("release")) != (target.namespace, target.release):
        raise LifecycleError("simulator plan has a different namespace or release")
    if not DIGEST.fullmatch(str(plan.get("rendered", ""))):
        raise LifecycleError("simulator plan lacks a rendered artifact digest")
    return record, plan


def plan_release(target: Target, env: dict[str, str]) -> dict:
    """Create a reviewed plan containing artifact digests and the observed prior release."""
    desired = _candidate(target)
    registry = Registry(target, env)
    values = _values(target, desired, env)
    before = snapshot(target)
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work)
        chart = _pull_chart(registry, target, directory, desired["version"], desired["chartDigest"])
        config = directory / "values.json"
        config.write_bytes(_canonical(values))
        rendered = _render(target, chart, config)
        run(
            "kubectl", "-n", target.namespace, "apply", "--dry-run=server", "-f", "-",
            data=rendered.encode(), timeout=120,
        )
    plan = {
        "namespace": target.namespace, "release": target.release, "desired": desired,
        "before": before, "values": values, "rendered": f"sha256:{hashlib.sha256(rendered.encode()).hexdigest()}",
    }
    return write_receipt(target, "plan", plan=plan, fingerprint=_fingerprint(plan))


def _mutation_checks(target: Target) -> None:
    """Run the existing registry-backed mutation gates with validated plan identity."""
    run(
        "bash", "scripts/check.sh", "--profile", "deploy", timeout=120,
        extra_env={
            "MODULE": "dmtf-sim", "PLAN_ARTIFACT": str(REPORTS / "plan.json"),
            "PLAN_COMMIT": target.commit,
        },
    )


def apply_release(target: Target, env: dict[str, str]) -> dict:
    """Verify every plan input before installing and waiting for the pinned release."""
    record, plan = _load_plan(target)
    desired = _candidate(target)
    values = _values(target, desired, env)
    if plan["desired"] != desired or plan["values"] != values:
        raise LifecycleError("apply inputs disagree with the sealed simulator plan")
    _mutation_checks(target)
    observed = snapshot(target)
    if observed != plan["before"]:
        # A previous attempt may have applied this exact plan before losing its
        # receipt. Accept only when the live Helm record proves that fingerprint.
        if observed is None or _installed_plan(target) != f"plan={record['fingerprint']}":
            raise LifecycleError("the release changed after planning; create a fresh plan")
        return write_receipt(
            target, "apply", fingerprint=record["fingerprint"], before=plan["before"],
            after=_check_candidate(target, observed, desired), reused=True,
        )
    registry = Registry(target, env)
    if registry.image_digest(target) != desired["image"].rsplit("@", 1)[-1]:
        raise LifecycleError("Harbor no longer holds the planned simulator image")
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work)
        chart = _pull_chart(registry, target, directory, desired["version"], desired["chartDigest"])
        config = directory / "values.json"
        config.write_bytes(_canonical(values))
        rendered = _render(target, chart, config)
        if f"sha256:{hashlib.sha256(rendered.encode()).hexdigest()}" != plan["rendered"]:
            raise LifecycleError("apply render differs from the immutable plan")
        run(
            "helm", "upgrade", "--install", target.release, str(chart),
            "--namespace", target.namespace, "--values", str(config),
            "--atomic", "--wait", "--timeout", "4m", "--history-max", "3",
            "--description", f"plan={record['fingerprint']}", timeout=540,
        )
    after = _check_candidate(target, snapshot(target), desired)
    return write_receipt(
        target, "apply", fingerprint=record["fingerprint"], before=observed,
        after=after, reused=False,
    )


def _plan_stage(target: Target, name: str, fingerprint: str) -> dict:
    """Load a downstream receipt associated with the current sealed plan."""
    record = load_receipt(target, name)
    if record.get("fingerprint") != fingerprint:
        raise LifecycleError(f"{name} evidence refers to a different deployment plan")
    return record


def verify_release(target: Target, _env: dict[str, str]) -> dict:
    """Verify cluster state independently after the apply job completed."""
    record, plan = _load_plan(target)
    apply = _plan_stage(target, "apply", record["fingerprint"])
    observed = _check_candidate(target, snapshot(target), plan["desired"])
    if apply.get("after") != observed:
        raise LifecycleError("independent simulator read-back disagrees with the apply receipt")
    return write_receipt(target, "verify", fingerprint=record["fingerprint"], observation=observed)


def live_test(target: Target, _env: dict[str, str]) -> dict:
    """Run the existing Redfish live test and require a non-skipped service-root case."""
    record, plan = _load_plan(target)
    verify = _plan_stage(target, "verify", record["fingerprint"])
    observed = _check_candidate(target, snapshot(target), plan["desired"])
    if verify.get("observation") != observed:
        raise LifecycleError("simulator changed before the service-root test")
    with tempfile.TemporaryDirectory() as work:
        report = Path(work) / "junit.xml"
        host = f"{target.release}.{target.namespace}.svc"
        run(
            sys.executable, "-m", "pytest", "-q", "-m", "dmtf_sim_live",
            "tests/k8s/test_dmtf_sim_live.py::test_dmtf_service_root",
            "-o", "junit_logging=no", f"--junitxml={report}", timeout=100,
            extra_env={"REDFISH_IP": host, "REDFISH_PORT": str(observed["servicePort"])},
        )
        parsed = ET.parse(report).getroot()
        cases = parsed.findall(".//testcase")
        if len(cases) != 1 or any(
            cases[0].find(tag) is not None for tag in ("failure", "error", "skipped")
        ):
            raise LifecycleError("the Redfish live test has no passing service-root case")
    return write_receipt(
        target, "live-test", fingerprint=record["fingerprint"], observation=observed,
        path="/redfish/v1/", tests=1, failures=0, skipped=0,
    )


def _without_revision(record: dict | None) -> dict | None:
    """Compare release identity regardless of the Helm history counter."""
    return None if record is None else {key: value for key, value in record.items() if key != "revision"}


def rollback_release(target: Target, _env: dict[str, str]) -> dict:
    """Restore the exact prior release or remove an owned first installation."""
    record, plan = _load_plan(target)
    applied = _plan_stage(target, "apply", record["fingerprint"])
    prior = plan["before"]
    observed = snapshot(target)
    _mutation_checks(target)
    if _without_revision(observed) == _without_revision(prior):
        return write_receipt(
            target, "rollback", fingerprint=record["fingerprint"], before=observed,
            after=observed, reused=True, remainingResources=[],
        )
    if observed != applied.get("after"):
        raise LifecycleError("refusing to roll back a release modified by another pipeline")
    _check_candidate(target, observed, plan["desired"])
    if prior is None:
        run(
            "helm", "uninstall", target.release, "--namespace", target.namespace,
            "--wait", "--timeout", "4m", timeout=280,
        )
    else:
        run(
            "helm", "rollback", target.release, str(prior["revision"]),
            "--namespace", target.namespace, "--wait", "--timeout", "4m", timeout=280,
        )
    after = snapshot(target)
    if _without_revision(after) != _without_revision(prior):
        raise LifecycleError("rollback did not restore the independently verified prior state")
    return write_receipt(
        target, "rollback", fingerprint=record["fingerprint"], before=observed, after=after,
        reused=False, remainingResources=[],
    )


def release_evidence(target: Target, _env: dict[str, str]) -> dict:
    """Aggregate independent exact-plan receipts and scan the sanitized evidence."""
    record, plan = _load_plan(target)
    apply = _plan_stage(target, "apply", record["fingerprint"])
    verify = _plan_stage(target, "verify", record["fingerprint"])
    smoke = _plan_stage(target, "live-test", record["fingerprint"])
    observed = _check_candidate(target, snapshot(target), plan["desired"])
    if (
        apply.get("after") != observed or verify.get("observation") != observed
        or smoke.get("observation") != observed or smoke.get("tests") != 1
        or smoke.get("failures") != 0 or smoke.get("skipped") != 0
    ):
        raise LifecycleError("complete independent simulator release evidence is unavailable")
    try:
        evidence = write_receipt(
            target, "release", fingerprint=record["fingerprint"],
            artifacts=plan["desired"], before=plan["before"], after=observed,
            verifyJob=verify["job"], liveTestJob=smoke["job"], remainingResources=[],
        )
        run("bash", "scripts/gates/evidence/sanitized.sh", extra_env={"EVIDENCE_DIR": str(REPORTS)})
    except LifecycleError:
        (REPORTS / "release.json").unlink(missing_ok=True)
        raise
    return evidence


def validate(target: Target, env: dict[str, str]) -> dict:
    """Check static inputs and the tools used by the simulator delivery adapters."""
    for name in ("git", "helm", "oc", "kubectl"):
        if shutil.which(name) is None:
            raise LifecycleError(f"toolbox lacks {name}")
    bundle, _ = _bundle(target)
    if not (DOCKERFILE.is_file() and bundle.is_file() and SIMULATOR.is_file()):
        raise LifecycleError("required simulator build inputs are unavailable")
    run("git", "diff", "--quiet", target.commit, "--", str(CHART), str(CONTRACT))
    _required(env, "REDFISH_HARBOR_BUILD_SECRET", DNS_LABEL)
    _required(env, "REDFISH_HARBOR_PULL_SECRET", DNS_LABEL)
    return {"status": "validated", **target.identity()}


STAGES = {
    "publish-image": publish_image, "publish-chart": publish_chart, "plan": plan_release,
    "apply": apply_release, "verify": verify_release, "live-test": live_test,
    "evidence": release_evidence, "rollback": rollback_release, "validate": validate,
}


def _interrupt(_sig: int, _frame: object) -> None:
    """Permit cleanup of owned resources when the job receives a termination signal."""
    raise KeyboardInterrupt


def main() -> int:
    """Run a protected CI stage with stable output and fail-closed diagnostics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report intent without contacting live APIs")
    mode.add_argument("--apply", action="store_true", help="execute the explicitly selected mutating stage")
    args = parser.parse_args()
    if args.apply and args.stage not in MUTATIONS:
        parser.error("--apply is valid only for a mutating stage")
    try:
        env = dict(os.environ)
        if args.dry_run or (args.stage in MUTATIONS and not args.apply):
            print(json.dumps({"status": "dry-run", "stage": args.stage}, sort_keys=True))
            return 0
        for handled in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(handled, _interrupt)
        target = Target.from_environment(env)
        result = STAGES[args.stage](target, env)
        print(json.dumps(result, sort_keys=True))
        return 0
    except LifecycleError as exc:
        print(f"BLOCKER: {exc}", file=sys.stderr)
    except (OSError, KeyError, StopIteration, TypeError, ValueError, yaml.YAMLError, tarfile.TarError):
        print("BLOCKER: malformed or unavailable simulator lifecycle input", file=sys.stderr)
    except KeyboardInterrupt:
        print("BLOCKER: simulator lifecycle interrupted", file=sys.stderr)
        return 130
    print(
        "SAFE_NEXT_STEP: inspect the project-owned stage inputs and run again on the protected runner",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    sys.exit(main())
