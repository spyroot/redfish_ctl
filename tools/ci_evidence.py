#!/usr/bin/env python3
"""Build, validate, and write exact-identity CI evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WARNING_SUMMARY_RE = re.compile(r"\b(\d+) warnings?\b", re.IGNORECASE)
_SKIP_REASON_RE = re.compile(r"^SKIPPED \[(\d+)\].*?: (.+)$", re.MULTILINE)
_OPTIONAL_SKIP_RE = re.compile(
    r"no IDRAC_IP|REDFISH_EMULATOR|HPE_EMULATOR|LFS pointer|"
    r"no @odata\.type|OEM type / no standard schema",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"BEGIN [A-Z ]*PRIVATE KEY|X-SF-Token:|"
    r"[Bb]earer [A-Za-z0-9._-]{20,}|glpat-[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{30,}|password[\"' :=]+[^ *]{6,}"
)


class EvidenceError(ValueError):
    """Raised when required exact-identity evidence cannot be established."""


def _required(env: Mapping[str, str], name: str) -> str:
    """Return one non-empty environment value or raise a classified error.

    :param env: environment mapping.
    :param name: required key.
    :return: stripped value.
    :raises EvidenceError: when the key is empty or absent.
    """
    value = env.get(name, "").strip()
    if not value:
        raise EvidenceError(f"required CI identity {name} is missing")
    return value


def _commit(env: Mapping[str, str], root: Path) -> str:
    """Resolve CI identity and verify it against independent Git read-back.

    :param env: environment mapping.
    :param root: repository root.
    :return: lowercase 40-character commit SHA.
    :raises EvidenceError: when the resolved identity is not exact.
    """
    value = (env.get("CI_COMMIT_SHA") or env.get("GITHUB_SHA") or "").strip()
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    read_back = proc.stdout.strip() if proc.returncode == 0 else ""
    if not _COMMIT_RE.fullmatch(read_back):
        raise EvidenceError("exact Git HEAD read-back is unavailable")
    if value and read_back and value != read_back:
        raise EvidenceError("CI commit identity does not match Git HEAD read-back")
    value = value or read_back
    if not _COMMIT_RE.fullmatch(value):
        raise EvidenceError("exact 40-character commit identity is unavailable")
    return value


def _standards_revision(root: Path) -> str:
    """Read the exact Standards revision from the tracked project binding.

    :param root: repository root.
    :return: lowercase 40-character Standards SHA.
    :raises EvidenceError: when the binding is missing or non-exact.
    """
    binding = yaml.safe_load((root / "standards-binding.yaml").read_text(encoding="utf-8"))
    revision = str(binding.get("spec", {}).get("source", {}).get("revision", ""))
    if not _COMMIT_RE.fullmatch(revision):
        raise EvidenceError("standards-binding.yaml does not pin an exact revision")
    return revision


def _runner_digest(env: Mapping[str, str]) -> str:
    """Resolve the immutable approved runner image digest.

    :param env: environment mapping supplied by the protected runner/provider.
    :return: sha256 digest.
    :raises EvidenceError: when no immutable digest is available.
    """
    value = (
        env.get("CI_JOB_IMAGE_DIGEST")
        or env.get("PROJECT_CI_IMAGE_DIGEST")
        or env.get("TOOLBOX_VALIDATION_IMAGE_DIGEST")
        or ""
    ).strip()
    if not value:
        image = env.get("CI_JOB_IMAGE", "").strip()
        if "@" in image:
            value = image.rsplit("@", 1)[1]
    if not _DIGEST_RE.fullmatch(value):
        raise EvidenceError("approved runner image digest is missing or not immutable")
    return value


def _identity(env: Mapping[str, str], root: Path) -> dict[str, object]:
    """Return the immutable CI identity shared by all evidence documents."""
    return {
        "job": _required(env, "CI_JOB_NAME"),
        "job_id": _required(env, "CI_JOB_ID"),
        "pipeline_id": _required(env, "CI_PIPELINE_ID"),
        "commit": _commit(env, root),
        "standards_revision": _standards_revision(root),
        "runner_digest": _runner_digest(env),
        "artifact_digest": None,
    }


def build_evidence(
    *,
    kind: str,
    name: str,
    command: str,
    status: str,
    return_code: int,
    observations: Mapping[str, object],
    env: Mapping[str, str] | None = None,
    root: Path = REPO_ROOT,
) -> dict:
    """Build one gate or CI-job evidence document.

    :param kind: ``gate`` or ``job``.
    :param name: registered gate or CI job name.
    :param command: executed guarded entrypoint.
    :param status: ``passed`` or ``failed``.
    :param return_code: observed command exit status.
    :param observations: output, skip, cleanup, and sanitization observations.
    :param env: CI identity environment; defaults to ``os.environ``.
    :param root: repository root containing the Standards binding.
    :return: schema-valid evidence mapping.
    :raises EvidenceError: when identity or input is incomplete.
    """
    selected_env = os.environ if env is None else env
    if kind not in {"gate", "job"}:
        raise EvidenceError(f"unsupported evidence kind: {kind}")
    if not _SAFE_NAME_RE.fullmatch(name):
        raise EvidenceError(f"unsafe evidence name: {name}")
    if status not in {"passed", "failed"}:
        raise EvidenceError(f"unsupported evidence status: {status}")
    if return_code < 0:
        raise EvidenceError("return_code must be non-negative")
    warnings = observations.get("warnings")
    skipped = observations.get("skipped_required_tests")
    optional_skips = observations.get("skipped_optional_tests")
    cleanup_status = observations.get("cleanup_status")
    remaining = observations.get("remaining")
    sources = observations.get("sources")
    if not isinstance(warnings, int) or isinstance(warnings, bool) or warnings < 0:
        raise EvidenceError("observed warning count is invalid")
    if not isinstance(skipped, int) or isinstance(skipped, bool) or skipped < 0:
        raise EvidenceError("observed required-skip count is invalid")
    if not isinstance(optional_skips, int) or isinstance(optional_skips, bool) or optional_skips < 0:
        raise EvidenceError("observed optional-skip count is invalid")
    if cleanup_status not in {"passed", "failed"}:
        raise EvidenceError("observed cleanup status is invalid")
    if not isinstance(remaining, list) or not all(isinstance(item, str) for item in remaining):
        raise EvidenceError("observed cleanup remainder is invalid")
    if not isinstance(sources, Mapping) or not all(
        isinstance(sources.get(key), str) and sources[key]
        for key in ("warnings", "skips", "cleanup", "sanitization")
    ):
        raise EvidenceError("evidence observation sources are incomplete")
    effective_status = status
    effective_code = return_code
    if warnings or skipped or cleanup_status != "passed" or remaining:
        effective_status = "failed"
        effective_code = effective_code or 1
    evidence = {
        "schema": "redfish_ctl.ci_evidence/v1",
        "kind": kind,
        "name": name,
        **_identity(selected_env, root),
        "command": command,
        "status": effective_status,
        "return_code": effective_code,
        "warnings": warnings,
        "skipped_required_tests": skipped,
        "skipped_optional_tests": optional_skips,
        "cleanup": {"status": cleanup_status, "remaining": list(remaining)},
        "observation_sources": dict(sources),
        "evidence_sanitized": False,
    }
    return evidence


def _required_tools(tools: Sequence[str]) -> list[str]:
    """Return required commands that are absent from ``PATH``."""
    return [tool for tool in tools if shutil.which(tool) is None]


def observe_gate(
    *,
    command: str,
    root: Path = REPO_ROOT,
    timeout_seconds: int = 3600,
) -> dict:
    """Run one real gate and return output-derived policy observations."""
    before = _tracked_state(root)
    try:
        completed = subprocess.run(
            [command],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        return_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return_code = 124
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=os.sys.stderr)
    combined = f"{stdout}\n{stderr}"
    warning_counts = [int(value) for value in _WARNING_SUMMARY_RE.findall(combined)]
    warnings = max(warning_counts, default=0)
    required_skips = 0
    optional_skips = 0
    for count_text, reason in _SKIP_REASON_RE.findall(combined):
        count = int(count_text)
        if _OPTIONAL_SKIP_RE.search(reason):
            optional_skips += count
        else:
            required_skips += count
    after = _tracked_state(root)
    remaining = sorted(set(after) - set(before))
    return {
        "return_code": return_code,
        "warnings": warnings,
        "skipped_required_tests": required_skips,
        "skipped_optional_tests": optional_skips,
        "cleanup_status": "passed" if not remaining else "failed",
        "remaining": remaining,
        "sources": {
            "warnings": "captured gate output",
            "skips": "pytest -ra skip reasons; only declared optional reasons excluded",
            "cleanup": "git status tracked-state comparison",
            "sanitization": "quiet credential-pattern scan before atomic write",
        },
    }


def _tracked_state(root: Path) -> list[str]:
    """Read tracked working-tree changes for cleanup verification."""
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise EvidenceError("tracked-state read-back failed")
    return [line for line in completed.stdout.splitlines() if line]


def _source_tree_digest(root: Path) -> str:
    """Return a SHA-256 digest of the exact Git tree listing."""
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--full-tree", "HEAD"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise EvidenceError("candidate tree digest read-back failed")
    return "sha256:" + hashlib.sha256(completed.stdout).hexdigest()


def observe_smoke(
    *,
    record: Mapping[str, object],
    gate_observations: Mapping[str, object],
    env: Mapping[str, str] | None = None,
    root: Path = REPO_ROOT,
) -> dict:
    """Exercise the real guarded entrypoint and return observed smoke checks."""
    selected_env = dict(os.environ if env is None else env)
    required_tools = record.get("requiredTools")
    if not isinstance(required_tools, list) or not all(
        isinstance(tool, str) and tool for tool in required_tools
    ):
        raise EvidenceError("smoke inventory requiredTools is invalid")
    missing = _required_tools(required_tools)
    check_script = root / "scripts" / "check.sh"
    bash_path = shutil.which("bash")
    dirname_path = shutil.which("dirname")
    if missing or not bash_path or not dirname_path:
        missing_all = missing + [
            name
            for name, value in (("bash", bash_path), ("dirname", dirname_path))
            if value is None
        ]
        raise EvidenceError(f"required smoke tools are missing: {', '.join(missing_all)}")
    before = _tracked_state(root)
    first = subprocess.run(
        [str(check_script), "--list"],
        cwd=root,
        env=selected_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    second = subprocess.run(
        [str(check_script), "--list"],
        cwd=root,
        env=selected_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    with tempfile.TemporaryDirectory(prefix="redfish-ctl-smoke-") as temp:
        bin_dir = Path(temp) / "bin"
        bin_dir.mkdir()
        (bin_dir / "dirname").symlink_to(dirname_path)
        negative_env = {**selected_env, "PATH": str(bin_dir)}
        negative = subprocess.run(
            [bash_path, str(check_script), "--list"],
            cwd=root,
            env=negative_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    identity = _identity(selected_env, root)
    read_back = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    after = _tracked_state(root)
    remaining = sorted(set(after) - set(before))
    smoke_class = str(record.get("class", ""))
    protected_surface = smoke_class != "protected-live" or (
        selected_env.get("CI_SERVER_HOST") == "gitlab.rnd.embedings.ai"
        and bool(selected_env.get("CI_COMMIT_REF_PROTECTED") == "true")
    )
    return {
        "startup": True,
        "tools": not missing,
        "entrypoint": gate_observations.get("return_code") == 0,
        "negative_path": negative.returncode != 0 and "python3" in negative.stderr,
        "read_back": read_back.returncode == 0
        and read_back.stdout.strip() == identity["commit"],
        "bounded_timeout": True,
        "idempotent_noop": first.returncode == 0
        and second.returncode == 0
        and first.stdout == second.stdout,
        "protected_surface": protected_surface,
        "cleanup_status": "passed" if not remaining else "failed",
        "remaining": remaining,
        "candidate_digest": _source_tree_digest(root),
        "sources": {
            "startup": "running CI smoke process",
            "tools": "PATH lookup of inventory requiredTools",
            "entrypoint": "observed registered gate command exit status",
            "negative_path": "real scripts/check.sh --list with python3 removed from PATH",
            "read_back": "independent git rev-parse HEAD",
            "bounded_timeout": "30-second subprocess timeouts",
            "idempotent_noop": "two identical scripts/check.sh --list executions",
            "protected_surface": "Internal GitLab protected-ref environment assertion",
            "cleanup": "git status tracked-state comparison after temporary-directory cleanup",
            "sanitization": "quiet credential-pattern scan before atomic write",
        },
    }


def build_smoke_evidence(
    *,
    record: Mapping[str, object],
    gate_observations: Mapping[str, object],
    smoke_observations: Mapping[str, object],
    env: Mapping[str, str] | None = None,
    root: Path = REPO_ROOT,
) -> dict:
    """Build one fail-closed smoke result from its inventory record.

    The negative check invokes the same dependency validator with a guaranteed
    missing command, proving that a missing runtime dependency is non-green.
    """
    selected_env = os.environ if env is None else env
    job = str(record.get("job", ""))
    smoke_class = str(record.get("class", ""))
    command = str(record.get("command", ""))
    required_tools = record.get("requiredTools")
    artifact = record.get("artifactUnderTest")
    if not _SAFE_NAME_RE.fullmatch(job):
        raise EvidenceError(f"unsafe smoke job name: {job}")
    supported_classes = {
        "wiring",
        "offline-component",
        "ephemeral-integration",
        "protected-live",
        "recovery",
        "status-reflection",
    }
    if smoke_class not in supported_classes:
        raise EvidenceError(f"unsupported smoke class: {smoke_class}")
    if not command:
        raise EvidenceError("smoke inventory command is missing")
    if not isinstance(required_tools, list) or not all(
        isinstance(tool, str) and tool for tool in required_tools
    ):
        raise EvidenceError("smoke inventory requiredTools is invalid")
    if not isinstance(artifact, Mapping):
        raise EvidenceError("smoke inventory artifactUnderTest is invalid")
    identity = _identity(selected_env, root)
    check_names = (
        "startup",
        "tools",
        "entrypoint",
        "negative_path",
        "read_back",
        "bounded_timeout",
        "idempotent_noop",
        "protected_surface",
    )
    sources = smoke_observations.get("sources")
    if not isinstance(sources, Mapping):
        raise EvidenceError("smoke observation sources are missing")
    for check_name in check_names:
        if not isinstance(smoke_observations.get(check_name), bool):
            raise EvidenceError(f"smoke observation is missing: {check_name}")
        if not isinstance(sources.get(check_name), str) or not sources[check_name]:
            raise EvidenceError(f"smoke observation source is missing: {check_name}")
    return_code = gate_observations.get("return_code")
    warnings = gate_observations.get("warnings")
    required_skips = gate_observations.get("skipped_required_tests")
    optional_skips = gate_observations.get("skipped_optional_tests")
    if not isinstance(return_code, int) or return_code < 0:
        raise EvidenceError("observed smoke return code is invalid")
    if not isinstance(warnings, int) or warnings < 0:
        raise EvidenceError("observed smoke warning count is invalid")
    if not isinstance(required_skips, int) or required_skips < 0:
        raise EvidenceError("observed smoke required-skip count is invalid")
    if not isinstance(optional_skips, int) or optional_skips < 0:
        raise EvidenceError("observed smoke optional-skip count is invalid")
    cleanup_status = smoke_observations.get("cleanup_status")
    remaining = smoke_observations.get("remaining")
    if cleanup_status not in {"passed", "failed"} or not isinstance(remaining, list):
        raise EvidenceError("observed smoke cleanup is invalid")
    checks_passed = all(smoke_observations[name] for name in check_names)
    status = "passed" if (
        return_code == 0
        and warnings == 0
        and required_skips == 0
        and cleanup_status == "passed"
        and not remaining
        and checks_passed
    ) else "failed"
    evidence = {
        "schema": "redfish_ctl.smoke_evidence/v1",
        "kind": "smoke",
        "name": job,
        **identity,
        "smoke_class": smoke_class,
        "command": command,
        "return_code": return_code,
        "candidate": {
            "type": str(artifact.get("type", "")),
            "digest_source": "git-ls-tree-sha256",
            "digest": smoke_observations.get("candidate_digest"),
        },
        "checks": {
            name: {
                "status": "passed" if smoke_observations[name] else "failed",
                "source": sources[name],
            }
            for name in check_names
        },
        "tools": {
            "required": list(required_tools),
            "missing": [],
        },
        "status": status,
        "warnings": warnings,
        "skipped_required_tests": required_skips,
        "skipped_optional_tests": optional_skips,
        "cleanup": {"status": cleanup_status, "remaining": list(remaining)},
        "observation_sources": dict(sources),
        "evidence_sanitized": False,
    }
    return evidence


def write_evidence(
    path: Path,
    evidence: Mapping[str, object],
    *,
    root: Path = REPO_ROOT,
) -> dict:
    """Atomically write one bounded, sanitized evidence document.

    :param path: repository-local result path.
    :param evidence: validated evidence mapping.
    :return: finalized schema-validated evidence mapping.
    :raises EvidenceError: when the path escapes the repository reports tree.
    """
    resolved = path.resolve()
    reports_root = (root / "reports").resolve()
    if reports_root not in resolved.parents:
        raise EvidenceError("evidence output must remain under reports/")
    path.parent.mkdir(parents=True, exist_ok=True)
    finalized = dict(evidence)
    finalized["evidence_sanitized"] = True
    payload = json.dumps(finalized, sort_keys=True, separators=(",", ":")) + "\n"
    if _CREDENTIAL_RE.search(payload):
        raise EvidenceError("evidence contains secret-shaped content")
    schema_name = (
        "smoke-evidence.schema.json"
        if finalized.get("kind") == "smoke"
        else "ci-evidence.schema.json"
    )
    try:
        jsonschema.validate(
            finalized,
            json.loads((root / "schemas" / schema_name).read_text(encoding="utf-8")),
        )
    except jsonschema.ValidationError as exc:
        raise EvidenceError(f"evidence is invalid: {exc.message}") from exc
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
        read_back = json.loads(path.read_text(encoding="utf-8"))
        if read_back != finalized:
            raise EvidenceError("evidence read-back does not match the written result")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return finalized
