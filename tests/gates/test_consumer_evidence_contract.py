"""Offline regressions for exact CI evidence and Builder dispatch."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import jsonschema
import pytest
import yaml

from tools.ci_evidence import (
    EvidenceError,
    build_evidence,
    build_smoke_evidence,
    observe_gate,
    observe_smoke,
    select_release_blocking_smoke,
    smoke_timeout_seconds,
    write_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_SH = REPO_ROOT / "scripts" / "check.sh"
PROJECT_CI_ENTRYPOINT = REPO_ROOT / "scripts" / "project_ci_entrypoint.sh"
SANITIZER = REPO_ROOT / "scripts" / "gates" / "evidence" / "sanitized.sh"
CI_EVIDENCE_SCHEMA = REPO_ROOT / "schemas" / "ci-evidence.schema.json"
SMOKE_EVIDENCE_SCHEMA = REPO_ROOT / "schemas" / "smoke-evidence.schema.json"
DIGEST = "sha256:" + ("a" * 64)
COMMIT = "b" * 40
STANDARDS_REVISION = "c" * 40


def _yaml(path: Path) -> dict:
    """Load one tracked YAML mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _ci_env(job: str = "gate-merge") -> dict[str, str]:
    """Return complete immutable CI identity for deterministic evidence tests."""
    return {
        "CI_JOB_NAME": job,
        "CI_JOB_ID": "41",
        "CI_PIPELINE_ID": "17",
        "CI_COMMIT_SHA": COMMIT,
        "CI_JOB_IMAGE": f"registry.example/toolbox@{DIGEST}",
    }


def _root(tmp_path: Path) -> Path:
    """Create the minimum repository identity needed by evidence builders."""
    (tmp_path / ".gitlab-ci.yml").write_text(
        yaml.safe_dump(
            {"default": {"image": f"registry.example/toolbox@{DIGEST}"}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "standards-binding.yaml").write_text(
        yaml.safe_dump({"spec": {"source": {"revision": STANDARDS_REVISION}}}),
        encoding="utf-8",
    )
    (tmp_path / "schemas").mkdir()
    for schema in (CI_EVIDENCE_SCHEMA, SMOKE_EVIDENCE_SCHEMA):
        (tmp_path / "schemas" / schema.name).write_text(
            schema.read_text(encoding="utf-8"), encoding="utf-8"
        )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "ci@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "CI Evidence Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    return tmp_path


def _ci_env_for(root: Path, job: str = "gate-merge") -> dict[str, str]:
    """Return CI identity matching the fixture repository's exact HEAD."""
    env = _ci_env(job=job)
    env["CI_COMMIT_SHA"] = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    return env


def _gate_observations(return_code: int = 0) -> dict:
    """Return explicit observed policy inputs for evidence rendering tests."""
    return {
        "return_code": return_code,
        "timed_out": False,
        "timeout_seconds": 3600,
        "applied_timeout_seconds": [3600],
        "warnings": 0,
        "skipped_required_tests": 0,
        "skipped_optional_tests": 0,
        "cleanup_status": "passed",
        "remaining": [],
        "sources": {
            "warnings": "captured gate output",
            "skips": "pytest -ra skip reasons",
            "timeout": "inventory timeout applied to gate profile",
            "cleanup": "tracked-state comparison",
            "sanitization": "quiet scan before atomic write",
        },
    }


def _smoke_observations(*, entrypoint: bool = True) -> dict:
    """Return explicit smoke observations without inventing lifecycle results."""
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
    observations = {name: True for name in check_names}
    observations["entrypoint"] = entrypoint
    observations.update(
        {
            "cleanup_status": "passed",
            "remaining": [],
            "candidate_digest": DIGEST,
            "sources": {name: f"observed {name}" for name in check_names},
        }
    )
    observations["sources"].update(
        {
            "cleanup": "tracked-state comparison",
            "sanitization": "quiet scan before atomic write",
        }
    )
    return observations


def _artifact_paths(job: dict) -> set[str]:
    """Normalize one GitLab job's artifact paths."""
    paths = (job.get("artifacts") or {}).get("paths") or []
    return {paths} if isinstance(paths, str) else set(paths)


def _off_cluster_env() -> dict[str, str]:
    """Return a process environment outside the Kubernetes authority."""
    return {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "FOCUSED_GATE",
            "KUBERNETES_SERVICE_HOST",
            "KUBERNETES_SERVICE_PORT",
            "MERGE_PROFILE",
            "PROJECT_CI_PROFILE",
        }
    }


def _run_check(args: list[str]) -> subprocess.CompletedProcess:
    """Exercise only a fail-fast check.sh argument path."""
    return subprocess.run(
        [str(CHECK_SH), *args],
        cwd=REPO_ROOT,
        env=_off_cluster_env(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_gate_and_job_evidence_validate_and_read_back(tmp_path: Path) -> None:
    """Gate and job results carry exact identity and survive atomic read-back."""
    root = _root(tmp_path)
    schema = json.loads(CI_EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    for kind, name in (("gate", "unit.all"), ("job", "gate-merge")):
        evidence = build_evidence(
            kind=kind,
            name=name,
            command="scripts/gates/unit/all.sh",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=_ci_env_for(root),
            root=root,
        )
        output = root / "reports" / f"{kind}s" / f"{name}.json"
        finalized = write_evidence(output, evidence, root=root)
        jsonschema.validate(finalized, schema)
        assert json.loads(output.read_text(encoding="utf-8")) == finalized
        assert finalized["observation_sources"]["warnings"] == "captured gate output"
        assert finalized["skipped_optional_tests"] == 0


def test_provider_gate_index_covers_the_executable_registry() -> None:
    """Builder can resolve every gate without a second command registry."""
    manifest = _yaml(REPO_ROOT / "gates" / "manifest.yaml")
    executable = {record["id"]: record for record in manifest["gates"]}
    provider = {record["id"]: record for record in manifest["spec"]["gates"]}

    assert manifest["apiVersion"] == "homelab.embedings.ai/v1alpha1"
    assert manifest["kind"] == "GateRegistry"
    assert provider.keys() == executable.keys()
    for gate_id, record in executable.items():
        indexed = provider[gate_id]
        assert indexed["required"] is record["required"]
        assert indexed["mutation"] is record["mutates"]
        assert indexed["profiles"] == [record["profile"]]
        assert indexed["status"] == "active"
        assert indexed["output"] == f"reports/gates/{gate_id}.json"
        if record["profile"] == "merge" and not record["mutates"]:
            assert indexed["timeoutSeconds"] >= 60
            assert indexed["executionSurface"] in {
                "github",
                "internal-gitlab",
                "protected-internal-gitlab",
                "protected-kubernetes",
                "harbor",
            }


def test_runtime_skips_are_required_and_make_evidence_non_green(
    tmp_path: Path,
) -> None:
    """A runtime skip cannot be reclassified as optional required evidence."""
    root = _root(tmp_path)
    command = root / "skip-result.sh"
    command.write_text(
        "#!/bin/sh\nprintf 'SKIPPED [1] test_live.py: emulator unavailable\\n'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    observations = observe_gate(
        command=str(command),
        root=root,
        timeout_seconds=30,
    )
    evidence = build_evidence(
        kind="gate",
        name="unit.all",
        command=str(command),
        status="passed",
        return_code=0,
        observations=observations,
        env=_ci_env_for(root),
        root=root,
    )

    assert observations["skipped_required_tests"] == 1
    assert observations["skipped_optional_tests"] == 0
    assert evidence["status"] == "failed"
    assert evidence["return_code"] == 1

    tampered = _gate_observations()
    tampered["skipped_optional_tests"] = 1
    tampered_evidence = build_evidence(
        kind="gate",
        name="unit.all",
        command=str(command),
        status="passed",
        return_code=0,
        observations=tampered,
        env=_ci_env_for(root),
        root=root,
    )
    assert tampered_evidence["status"] == "failed"


def test_observe_gate_streams_output_before_the_command_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate progress is visible while the bounded command is still running."""
    root = _root(tmp_path)
    command = root / "stream.sh"
    command.write_text(
        "#!/bin/sh\nprintf 'first line\\n'\nsleep 1\nprintf 'second line\\n'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    first_seen = threading.Event()

    class LiveOutput(io.StringIO):
        def write(self, value: str) -> int:
            written = super().write(value)
            if "first line" in self.getvalue():
                first_seen.set()
            return written

    output = LiveOutput()
    monkeypatch.setattr("tools.ci_evidence.sys.stdout", output)
    observed: dict[str, dict] = {}
    worker = threading.Thread(
        target=lambda: observed.setdefault(
            "result",
            observe_gate(command=str(command), root=root, timeout_seconds=5),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert first_seen.wait(2), "first progress line was buffered"
        assert worker.is_alive(), "command finished before streaming was observed"
    finally:
        worker.join(5)

    assert not worker.is_alive()
    assert observed["result"]["return_code"] == 0
    assert output.getvalue() == "first line\nsecond line\n"


def test_observe_gate_timeout_stops_the_process_group(tmp_path: Path) -> None:
    """A timed-out gate returns bounded evidence without a surviving child."""
    root = _root(tmp_path)
    command = root / "timeout.sh"
    command.write_text(
        "#!/bin/sh\nprintf 'started\\n'\nsleep 5\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    observations = observe_gate(
        command=str(command),
        root=root,
        timeout_seconds=0.1,
    )

    assert observations["timed_out"] is True
    assert observations["return_code"] == 124


def test_smoke_timeout_comes_from_inventory_and_is_observed(
    tmp_path: Path,
) -> None:
    """The smoke timeout is validated and bound to the registered gate profile."""
    root = _root(tmp_path)
    scripts = root / "scripts"
    scripts.mkdir()
    check = scripts / "check.sh"
    check.write_text(
        "#!/bin/sh\n"
        "command -v python3 >/dev/null 2>&1 || { "
        "printf 'python3 missing\\n' >&2; exit 1; }\n"
        "printf 'unit.all\\n'\n",
        encoding="utf-8",
    )
    check.chmod(0o755)
    record = {
        "job": "gate-merge",
        "class": "wiring",
        "command": "./scripts/check.sh --profile merge",
        "requiredTools": ["sh"],
        "artifactUnderTest": {"type": "repository", "digestSource": "git-commit"},
        "timeoutSeconds": 1800,
    }
    gate_observations = _gate_observations()
    gate_observations["timeout_seconds"] = 1800
    gate_observations["applied_timeout_seconds"] = [1799.5, 1200]
    smoke_observations = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=_ci_env_for(root),
        root=root,
    )

    assert smoke_timeout_seconds(record) == 1800
    assert smoke_observations["bounded_timeout"] is True
    gate_observations["applied_timeout_seconds"] = [1800.1]
    mismatched = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=_ci_env_for(root),
        root=root,
    )
    assert mismatched["bounded_timeout"] is False
    with pytest.raises(EvidenceError, match="timeoutSeconds"):
        smoke_timeout_seconds({**record, "timeoutSeconds": 0})


def test_evidence_rejects_missing_or_mutable_runner_identity(tmp_path: Path) -> None:
    """A moving image tag or missing pipeline ID cannot become green evidence."""
    root = _root(tmp_path)
    env = _ci_env_for(root)
    env["CI_JOB_IMAGE"] = "registry.example/toolbox:latest"
    with pytest.raises(EvidenceError, match="digest"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )
    env = _ci_env_for(root)
    env["CI_JOB_IMAGE"] = "registry.example/toolbox@sha256:" + ("0" * 64)
    with pytest.raises(EvidenceError, match="tracked CI configuration"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )
    env = _ci_env_for(root)
    env["PROJECT_CI_IMAGE_DIGEST"] = "sha256:" + ("0" * 64)
    with pytest.raises(EvidenceError, match="disagrees with CI_JOB_IMAGE"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )
    env = _ci_env_for(root)
    del env["CI_JOB_IMAGE"]
    env["PROJECT_CI_IMAGE_DIGEST"] = DIGEST
    with pytest.raises(EvidenceError, match="CI_JOB_IMAGE"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )
    env = _ci_env_for(root)
    del env["CI_PIPELINE_ID"]
    with pytest.raises(EvidenceError, match="CI_PIPELINE_ID"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )
    env = _ci_env_for(root)
    env["CI_COMMIT_SHA"] = COMMIT
    with pytest.raises(EvidenceError, match="read-back"):
        build_evidence(
            kind="job",
            name="gate-merge",
            command="./scripts/check.sh --profile merge",
            status="passed",
            return_code=0,
            observations=_gate_observations(),
            env=env,
            root=root,
        )


def test_wiring_smoke_proves_tools_negative_path_and_cleanup(tmp_path: Path) -> None:
    """Wiring smoke records every mandatory safe check with immutable identity."""
    root = _root(tmp_path)
    record = {
        "job": "gate-merge",
        "class": "wiring",
        "command": "./scripts/check.sh --profile merge",
        "requiredTools": ["sh"],
        "artifactUnderTest": {"type": "repository", "digestSource": "git-commit"},
    }
    evidence = build_smoke_evidence(
        record=record,
        gate_observations=_gate_observations(),
        smoke_observations=_smoke_observations(),
        env=_ci_env_for(root),
        root=root,
    )
    output = root / "reports" / "smoke" / "gate-merge.json"
    evidence = write_evidence(output, evidence, root=root)
    schema = json.loads(SMOKE_EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(evidence, schema)
    assert evidence["checks"]["negative_path"]["status"] == "passed"
    assert evidence["cleanup"]["remaining"] == []
    assert evidence["candidate"]["digest"] == DIGEST

    incomplete = _smoke_observations()
    del incomplete["sources"]["negative_path"]
    with pytest.raises(EvidenceError, match="source"):
        build_smoke_evidence(
            record=record,
            gate_observations=_gate_observations(),
            smoke_observations=incomplete,
            env=_ci_env_for(root),
            root=root,
        )


def test_protected_live_smoke_records_failed_read_back(tmp_path: Path) -> None:
    """A failed protected-live gate remains valid non-green smoke evidence."""
    root = _root(tmp_path)
    record = {
        "job": "gate-integration",
        "class": "protected-live",
        "command": "./scripts/check.sh --profile integration",
        "requiredTools": ["sh"],
        "artifactUnderTest": {"type": "repository", "digestSource": "git-commit"},
    }
    evidence = build_smoke_evidence(
        record=record,
        gate_observations=_gate_observations(return_code=7),
        smoke_observations=_smoke_observations(entrypoint=False),
        env=_ci_env_for(root, job="gate-integration"),
        root=root,
    )
    evidence = write_evidence(
        root / "reports" / "smoke" / "gate-integration.json",
        evidence,
        root=root,
    )
    assert evidence["status"] == "failed"
    assert evidence["return_code"] == 7
    assert evidence["checks"]["entrypoint"] == {
        "status": "failed",
        "source": "observed entrypoint",
    }


def test_evidence_sanitization_happens_before_atomic_write(tmp_path: Path) -> None:
    """Secret-shaped content never reaches the final evidence path or diagnostics."""
    root = _root(tmp_path)
    evidence = build_evidence(
        kind="job",
        name="gate-merge",
        command="password: hunter2hunter2",
        status="passed",
        return_code=0,
        observations=_gate_observations(),
        env=_ci_env_for(root),
        root=root,
    )
    output = root / "reports" / "ci" / "gate-merge.json"
    with pytest.raises(EvidenceError, match="secret-shaped"):
        write_evidence(output, evidence, root=root)
    assert not output.exists()


def test_focused_execution_cannot_emit_release_blocking_smoke() -> None:
    """A narrowed gate cannot publish smoke that represents a full profile."""
    inventory = {
        "spec": {
            "smokeTests": [
                {
                    "job": "project-ci-cpu-validation",
                    "class": "wiring",
                }
            ]
        }
    }
    assert (
        select_release_blocking_smoke(
            inventory,
            job_name="project-ci-cpu-validation",
            selected_gate="unit.all",
        )
        is None
    )
    selected = select_release_blocking_smoke(
        inventory,
        job_name="project-ci-cpu-validation",
        selected_gate=None,
    )
    assert selected == inventory["spec"]["smokeTests"][0]


def test_required_jobs_publish_exact_job_and_smoke_evidence() -> None:
    """Every required job publishes its own schema-backed evidence paths."""
    registry = _yaml(REPO_ROOT / "gates/manifest.yaml")
    ci = _yaml(REPO_ROOT / ".gitlab-ci.yml")
    smoke = _yaml(REPO_ROOT / "inventory/ci/smoke-tests.yaml")
    smoke_jobs = {record["job"] for record in smoke["spec"]["smokeTests"]}
    assert ci["gate-merge"]["script"] == [
        "./scripts/check.sh --list",
        "./scripts/check.sh --profile merge",
    ]
    for job_name in registry["required_jobs"]:
        assert job_name in ci
        paths = _artifact_paths(ci[job_name])
        assert f"reports/ci/{job_name}.json" in paths
        assert f"reports/smoke/{job_name}.json" in paths
        assert job_name in smoke_jobs


def test_evidence_sanitizer_rejects_missing_evidence(tmp_path: Path) -> None:
    """An absent evidence tree is a failure, not a clean scan."""
    missing = tmp_path / "reports"
    proc = subprocess.run(
        [str(SANITIZER)],
        cwd=REPO_ROOT,
        env={**os.environ, "EVIDENCE_DIR": str(missing)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 1
    assert "missing" in (proc.stdout + proc.stderr).lower()


def test_dispatch_rejects_raw_argument_without_echoing_it() -> None:
    """Unknown raw values never enter dispatch argv or diagnostics."""
    secret_shaped_value = "gl" + "pat-" + ("A" * 24)
    proc = _run_check(
        ["--profile", "merge", "--gate", "unit.all", "--dispatch", secret_shaped_value]
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2
    assert secret_shaped_value not in combined
    assert "unexpected argument: --dispatch" not in combined


def test_dispatch_consumes_builder_full_profile_and_provider_credentials() -> None:
    """The wrapper maps merge to Builder full and never handles token values."""
    source = CHECK_SH.read_text(encoding="utf-8")
    assert "args+=(--profile full)" in source
    assert "GITLAB_PROJECT_TOKEN" not in source
    assert "--confirm-project-ci-run" in source
    assert "args+=(--dry-run)" in source
    assert 'args+=("${dispatch_args[@]}")' in source
    for option in ("--no-wait", "--log-format", "--log-level", "--run-id", "--timeout"):
        assert option in source

    proc = _run_check(["--profile", "merge", "--timeout", "30"])
    assert proc.returncode == 2
    assert "require --dispatch" in (proc.stdout + proc.stderr)

    proc = _run_check(["--profile", "merge", "--dispatch", "--apply"])
    assert proc.returncode == 2
    assert "requires --confirm-project-ci-run" in (proc.stdout + proc.stderr)

    proc = _run_check(
        [
            "--profile",
            "merge",
            "--dispatch",
            "--dry-run",
            "--apply",
            "--confirm-project-ci-run",
        ]
    )
    assert proc.returncode == 2
    assert "mutually exclusive" in (proc.stdout + proc.stderr)

    proc = _run_check(["--list", "--timeout", "30"])
    assert proc.returncode == 2
    assert "cannot be combined" in (proc.stdout + proc.stderr)


def test_project_ci_entrypoint_rejects_conflicting_selectors() -> None:
    """The imported Builder job cannot accept two independent selectors."""
    proc = subprocess.run(
        [str(PROJECT_CI_ENTRYPOINT)],
        cwd=REPO_ROOT,
        env={
            **_off_cluster_env(),
            "FOCUSED_GATE": "unit.all",
            "PROJECT_CI_PROFILE": "full",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 2
    assert "mutually exclusive" in (proc.stdout + proc.stderr)


def test_project_ci_entrypoint_accepts_focused_builder_profile(tmp_path: Path) -> None:
    """The Builder focused profile delegates the unit.all default gate."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    adapter = scripts / PROJECT_CI_ENTRYPOINT.name
    shutil.copyfile(PROJECT_CI_ENTRYPOINT, adapter)
    adapter.chmod(0o755)
    check = scripts / "check.sh"
    check.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    check.chmod(0o755)
    proc = subprocess.run(
        [str(adapter)],
        cwd=tmp_path,
        env={
            **_off_cluster_env(),
            "PROJECT_CI_PROFILE": "focused",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["--profile", "merge", "--gate", "unit.all"]


def test_project_ci_entrypoint_help_needs_no_external_commands() -> None:
    """Help is a dependency-free contract inspection, never a gate execution."""
    proc = subprocess.run(
        ["/bin/bash", str(PROJECT_CI_ENTRYPOINT), "--help"],
        cwd=REPO_ROOT,
        env={"PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0
    assert "usage: project_ci_entrypoint.sh" in proc.stdout
    assert proc.stderr == ""


def test_project_ci_entrypoint_dry_run_supports_sanitized_logging(
    tmp_path: Path,
) -> None:
    """Dry-run emits a machine result and secures optional diagnostics."""
    log_file = tmp_path / "selector.jsonl"
    proc = subprocess.run(
        [
            str(PROJECT_CI_ENTRYPOINT),
            "--dry-run",
            "--log-format",
            "json",
            "--log-level",
            "info",
            "--log-file",
            str(log_file),
            "--run-id",
            "ci-17",
        ],
        cwd=REPO_ROOT,
        env={**_off_cluster_env(), "PROJECT_CI_PROFILE": "focused"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {
        "status": "dry-run",
        "profile": "merge",
        "gate": "unit.all",
        "run_id": "ci-17",
    }
    diagnostic = json.loads(proc.stderr)
    assert diagnostic["component"] == "project-ci-entrypoint"
    assert diagnostic["result"] == "unit.all"
    assert json.loads(log_file.read_text(encoding="utf-8")) == diagnostic
    assert log_file.stat().st_mode & 0o777 == 0o600


def test_project_ci_entrypoint_rejects_unknown_flags_without_reflection() -> None:
    """Unknown input fails before selection and is not copied into diagnostics."""
    unsafe = "gl" + "pat-" + ("A" * 24)
    proc = subprocess.run(
        [str(PROJECT_CI_ENTRYPOINT), unsafe],
        cwd=REPO_ROOT,
        env=_off_cluster_env(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 2
    assert unsafe not in (proc.stdout + proc.stderr)
    assert "unexpected argument" in proc.stderr


def test_unit_profile_excludes_inapplicable_lanes_instead_of_skipping() -> None:
    """Required unit evidence deselects live and vendored-schema-only tests."""
    source = (REPO_ROOT / "scripts" / "gates" / "unit" / "all.sh").read_text(
        encoding="utf-8"
    )
    assert '-m "not live and not dmtf_sim_live"' in source
    assert "--ignore=tests/gates/test_redfish_schema.py" in source


def test_schema_gate_owns_exact_standards_and_provider_validation() -> None:
    """The schema gate fetches exact authorities and validates both bindings."""
    source = (REPO_ROOT / "tools" / "schema_gate.py").read_text(encoding="utf-8")
    assert "project-standards-binding.schema.json" in source
    assert "project-provider-binding.schema.json" in source
    assert "GIT_TERMINAL_PROMPT" in source
    assert "credential.helper=" in source

    binding = _yaml(REPO_ROOT / "standards-binding.yaml")
    provider = _yaml(REPO_ROOT / binding["spec"]["providers"][0]["binding"])
    ci = _yaml(REPO_ROOT / ".gitlab-ci.yml")
    manifest = _yaml(REPO_ROOT / "gates/manifest.yaml")
    include_identities = {
        (record["project"], record["ref"], record["file"])
        for record in ci["include"]
    }
    trusted_identities = {
        (record["project"], record["ref"], record["file"])
        for record in manifest["trusted_includes"]
    }
    resource_identity = (
        provider["spec"]["dispatch"]["project"],
        provider["spec"]["source"]["revision"],
        "/ci/templates/project-ci-resource-jobs.yml",
    )
    assert include_identities == trusted_identities
    assert resource_identity in include_identities
    assert "@sha256:" in ci["default"]["image"]
    assert ci["variables"]["PROJECT_CI_CPU_COMMAND"] == (
        "./scripts/project_ci_entrypoint.sh"
    )
