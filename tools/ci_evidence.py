#!/usr/bin/env python3
"""Build, validate, and write exact-identity CI evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

import jsonschema
import yaml

from tools.provider_contract import ProviderContractError, provider_host

REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WARNING_SUMMARY_RE = re.compile(r"\b(\d+) warnings?\b", re.IGNORECASE)
_ZERO_WARNING_RE = re.compile(
    r"\b(?:0 warnings?|warnings?\s*[:=]\s*0)\b",
    re.IGNORECASE,
)
_WARNING_DIAGNOSTIC_RE = re.compile(
    r"(?i)\bwarnings?\b|"
    r"\b(?:User|Runtime|Deprecation|Future|Resource)Warning\b|"
    r"\blevel\s*=\s*(?:warn|warning)\b"
)
_SKIP_REASON_RE = re.compile(r"^SKIPPED \[(\d+)\].*?: (.+)$", re.MULTILINE)
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


def _runner_digest(env: Mapping[str, str], root: Path) -> str:
    """Resolve and verify the immutable approved runner image digest.

    :param env: environment mapping supplied by the protected runner/provider.
    :param root: repository root containing the tracked CI image binding.
    :return: sha256 digest.
    :raises EvidenceError: when no immutable matching digest is available.
    """
    image = env.get("CI_JOB_IMAGE", "").strip()
    image_digest = image.rsplit("@", 1)[1] if "@" in image else ""
    asserted_digests = [
        env[name].strip()
        for name in (
            "CI_JOB_IMAGE_DIGEST",
            "PROJECT_CI_IMAGE_DIGEST",
            "TOOLBOX_VALIDATION_IMAGE_DIGEST",
        )
        if env.get(name, "").strip()
    ]
    if not image_digest:
        raise EvidenceError("CI_JOB_IMAGE must carry the immutable runner digest")
    if any(value != image_digest for value in asserted_digests):
        raise EvidenceError("runner digest assertion disagrees with CI_JOB_IMAGE")
    value = image_digest
    if not _DIGEST_RE.fullmatch(value):
        raise EvidenceError("approved runner image digest is missing or not immutable")
    ci = yaml.safe_load((root / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    configured_image = str((ci.get("default") or {}).get("image", ""))
    configured_digest = configured_image.rsplit("@", 1)[-1]
    if not _DIGEST_RE.fullmatch(configured_digest) or value != configured_digest:
        raise EvidenceError("runner image digest does not match tracked CI configuration")
    return value


def _identity(env: Mapping[str, str], root: Path) -> dict[str, object]:
    """Return the immutable CI identity shared by all evidence documents."""
    return {
        "job": _required(env, "CI_JOB_NAME"),
        "job_id": _required(env, "CI_JOB_ID"),
        "pipeline_id": _required(env, "CI_PIPELINE_ID"),
        "commit": _commit(env, root),
        "standards_revision": _standards_revision(root),
        "runner_digest": _runner_digest(env, root),
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
    output_sanitized = observations.get("output_sanitized")
    skipped = observations.get("skipped_required_tests")
    optional_skips = observations.get("skipped_optional_tests")
    cleanup_status = observations.get("cleanup_status")
    remaining = observations.get("remaining")
    sources = observations.get("sources")
    if not isinstance(warnings, int) or isinstance(warnings, bool) or warnings < 0:
        raise EvidenceError("observed warning count is invalid")
    if not isinstance(output_sanitized, bool):
        raise EvidenceError("observed output sanitization status is invalid")
    if not isinstance(skipped, int) or isinstance(skipped, bool) or skipped < 0:
        raise EvidenceError("observed required-skip count is invalid")
    if (
        not isinstance(optional_skips, int)
        or isinstance(optional_skips, bool)
        or optional_skips < 0
    ):
        raise EvidenceError("observed optional-skip count is invalid")
    if cleanup_status not in {"passed", "failed"}:
        raise EvidenceError("observed cleanup status is invalid")
    if not isinstance(remaining, list) or not all(isinstance(item, str) for item in remaining):
        raise EvidenceError("observed cleanup remainder is invalid")
    if not isinstance(sources, Mapping) or not all(
        isinstance(sources.get(key), str) and sources[key]
        for key in ("warnings", "skips", "timeout", "cleanup", "sanitization")
    ):
        raise EvidenceError("evidence observation sources are incomplete")
    effective_status = status
    effective_code = return_code
    if (
        warnings
        or not output_sanitized
        or skipped
        or optional_skips
        or cleanup_status != "passed"
        or remaining
    ):
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
        "evidence_sanitized": output_sanitized,
    }
    return evidence


def _required_tools(tools: Sequence[str]) -> list[str]:
    """Return required commands that are absent from ``PATH``."""
    return [tool for tool in tools if shutil.which(tool) is None]


def smoke_timeout_seconds(record: Mapping[str, object]) -> int:
    """Return one positive closed-world smoke timeout.

    :param record: one smoke inventory record.
    :return: positive timeout in seconds.
    :raises EvidenceError: when the record omits or corrupts the timeout.
    """
    timeout = record.get("timeoutSeconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise EvidenceError("smoke inventory timeoutSeconds must be a positive integer")
    return timeout


def gate_timeout_seconds(record: Mapping[str, object]) -> int:
    """Return one bounded provider gate timeout.

    :param record: one provider-facing gate record from ``gates/manifest.yaml``.
    :return: timeout from 60 through 3600 seconds.
    :raises EvidenceError: when the provider record omits or corrupts the timeout.
    """
    timeout = record.get("timeoutSeconds")
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 60 <= timeout <= 3600
    ):
        raise EvidenceError(
            "provider gate timeoutSeconds must be an integer from 60 through 3600"
        )
    return timeout


def select_release_blocking_smoke(
    inventory: Mapping[str, object],
    *,
    job_name: str,
    selected_gate: str | None,
) -> Mapping[str, object] | None:
    """Select one full-profile smoke record, suppressing narrowed runs.

    :param inventory: parsed closed-world smoke inventory.
    :param job_name: current CI job name.
    :param selected_gate: narrowed gate id, or ``None`` for a full profile.
    :return: the matching record only for a full-profile execution.
    :raises EvidenceError: when the inventory duplicates the job record.
    """
    spec = inventory.get("spec")
    records = spec.get("smokeTests", []) if isinstance(spec, Mapping) else []
    matches = [
        record
        for record in records
        if isinstance(record, Mapping) and record.get("job") == job_name
    ]
    if len(matches) > 1:
        raise EvidenceError(f"duplicate smoke inventory for {job_name}")
    if selected_gate is not None:
        return None
    return matches[0] if matches else None


def observe_gate(
    *,
    command: str,
    root: Path = REPO_ROOT,
    timeout_seconds: float = 3600,
) -> dict:
    """Run one real gate and return output-derived policy observations."""
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise EvidenceError("gate timeout must be positive")
    before = _tracked_state(root)
    output, return_code, timed_out = _run_captured_command(
        command=command,
        root=root,
        timeout_seconds=timeout_seconds,
    )
    output_sanitized = _CREDENTIAL_RE.search(output) is None
    if output_sanitized:
        sys.stdout.write(output)
    else:
        sys.stdout.write("gate output withheld: secret-shaped content detected\n")
    sys.stdout.flush()
    warning_counts = [int(value) for value in _WARNING_SUMMARY_RE.findall(output)]
    diagnostic_output = _ZERO_WARNING_RE.sub("", output)
    warnings = max(
        max(warning_counts, default=0),
        len(_WARNING_DIAGNOSTIC_RE.findall(diagnostic_output)),
    )
    required_skips = sum(
        int(count_text)
        for count_text, _reason in _SKIP_REASON_RE.findall(output)
    )
    after = _tracked_state(root)
    remaining = sorted(set(after) - set(before))
    return {
        "return_code": return_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "warnings": warnings,
        "output_sanitized": output_sanitized,
        "skipped_required_tests": required_skips,
        "skipped_optional_tests": 0,
        "cleanup_status": "passed" if not remaining else "failed",
        "remaining": remaining,
        "sources": {
            "warnings": "captured gate output",
            "skips": "pytest -ra skip reasons; every runtime skip is required",
            "timeout": "subprocess timeout applied to the registered gate command",
            "cleanup": "git status tracked and untracked-state comparison",
            "sanitization": (
                "captured gate output scanned before bounded publication and "
                "evidence scanned before atomic write"
            ),
        },
    }


def _run_captured_command(
    *,
    command: str | Sequence[str],
    root: Path,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> tuple[str, int, bool]:
    """Run one command while privately retaining its ordered output.

    Stdout and stderr share one pipe so their observed ordering is retained for
    evidence parsing. Output is returned only after the complete capture can be
    scanned for secret-shaped content. A new process group lets a timeout stop
    the gate and any child processes that inherited the output pipe.

    :param command: executable path or exact argument vector.
    :param root: working directory for the gate.
    :param timeout_seconds: positive wall-clock limit.
    :param env: optional subprocess environment; inherits the caller environment
        when omitted.
    :return: combined output, effective return code, and timeout flag.
    """
    command_args = [command] if isinstance(command, str) else list(command)
    if not command_args or not all(
        isinstance(value, str) and value for value in command_args
    ):
        raise EvidenceError("captured command argument vector is invalid")
    process = subprocess.Popen(
        command_args,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = process.communicate()

    return output, 124 if timed_out else process.returncode, timed_out


def _tracked_state(root: Path) -> list[str]:
    """Read tracked and untracked cleanup state outside declared reports."""
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise EvidenceError("tracked-state read-back failed")
    return [
        line
        for line in completed.stdout.splitlines()
        if line and not line[3:].startswith("reports/")
    ]


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
    smoke_class = str(record.get("class", ""))
    inventory_timeout = smoke_timeout_seconds(record)
    command = str(record.get("command", ""))
    try:
        command_args = shlex.split(command)
    except ValueError as exc:
        raise EvidenceError("smoke inventory command is not parseable") from exc
    if not command_args:
        raise EvidenceError("smoke inventory command is missing")
    probe_job = f"{selected_env.get('CI_JOB_NAME', 'smoke')}-probe"
    if not _SAFE_NAME_RE.fullmatch(probe_job):
        raise EvidenceError("derived smoke probe job name is unsafe")
    probe_env = {**selected_env, "CI_JOB_NAME": probe_job}
    probe_output, probe_return_code, probe_timed_out = _run_captured_command(
        command=command_args,
        root=root,
        timeout_seconds=inventory_timeout,
        env=probe_env,
    )
    probe_output_sanitized = _CREDENTIAL_RE.search(probe_output) is None
    after = _tracked_state(root)
    remaining = sorted(set(after) - set(before))
    applied_timeouts = gate_observations.get("applied_timeout_seconds")
    timed_out = gate_observations.get("timed_out")
    bounded_timeout = (
        isinstance(applied_timeouts, list)
        and bool(applied_timeouts)
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 < value <= inventory_timeout
            for value in applied_timeouts
        )
        and timed_out is False
    )
    protected_surface_source = (
        f"not applicable: smoke class {smoke_class} has no protected live surface"
    )
    protected_surface = smoke_class != "protected-live"
    if smoke_class == "protected-live":
        try:
            expected_provider_host = provider_host(root)
        except (OSError, ProviderContractError, yaml.YAMLError):
            expected_provider_host = ""
        protected_surface = (
            selected_env.get("CI_SERVER_HOST") == expected_provider_host
            and selected_env.get("CI_COMMIT_REF_PROTECTED") == "true"
        )
        protected_surface_source = (
            "Internal GitLab host and protected-ref environment assertion"
        )
    return {
        "startup": True,
        "tools": not missing,
        "entrypoint": gate_observations.get("return_code") == 0,
        "negative_path": negative.returncode != 0 and "python3" in negative.stderr,
        "read_back": read_back.returncode == 0
        and read_back.stdout.strip() == identity["commit"],
        "bounded_timeout": bounded_timeout,
        "idempotent_noop": gate_observations.get("return_code") == 0
        and probe_return_code == 0
        and not probe_timed_out
        and probe_output_sanitized
        and not remaining,
        "output_sanitized": probe_output_sanitized,
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
            "bounded_timeout": (
                f"observed per-gate subprocess bounds are positive and no greater "
                f"than inventory timeoutSeconds={inventory_timeout}; "
                f"timed_out={timed_out!r}"
            ),
            "idempotent_noop": (
                "registered smoke command passed initially and on one exact "
                "probe re-execution under a non-inventory CI job identity"
            ),
            "protected_surface": protected_surface_source,
            "cleanup": (
                "git status tracked and untracked-state comparison after "
                "temporary-directory cleanup; reports/ is the only exclusion"
            ),
            "sanitization": (
                "captured probe output and evidence scanned for credential patterns"
            ),
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
    output_sanitized = gate_observations.get("output_sanitized")
    probe_output_sanitized = smoke_observations.get("output_sanitized")
    required_skips = gate_observations.get("skipped_required_tests")
    optional_skips = gate_observations.get("skipped_optional_tests")
    if not isinstance(return_code, int) or return_code < 0:
        raise EvidenceError("observed smoke return code is invalid")
    if not isinstance(warnings, int) or warnings < 0:
        raise EvidenceError("observed smoke warning count is invalid")
    if not isinstance(output_sanitized, bool):
        raise EvidenceError("observed smoke output sanitization status is invalid")
    if not isinstance(probe_output_sanitized, bool):
        raise EvidenceError("observed smoke probe sanitization status is invalid")
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
        and output_sanitized
        and probe_output_sanitized
        and required_skips == 0
        and optional_skips == 0
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
        "evidence_sanitized": output_sanitized and probe_output_sanitized,
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
