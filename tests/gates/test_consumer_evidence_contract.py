"""Offline regressions for exact CI evidence and Builder dispatch."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import textwrap
import threading
import time
from pathlib import Path

import jsonschema
import pytest
import yaml

from tools import schema_gate
from tools.ci_evidence import (
    EvidenceError,
    build_evidence,
    build_smoke_evidence,
    gate_timeout_seconds,
    observe_gate,
    observe_smoke,
    select_release_blocking_smoke,
    smoke_timeout_seconds,
    write_evidence,
)
from tools.provider_contract import (
    ProviderContractError,
    provider_host,
    require_provider_repository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_SH = REPO_ROOT / "scripts" / "check.sh"
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


def _commit_local_authority(path: Path) -> str:
    """Create one local authority with committed and dirty working-tree states."""
    path.mkdir()
    commands = (
        ("git", "init", "--quiet", str(path)),
        ("git", "-C", str(path), "config", "user.email", "test@example.invalid"),
        ("git", "-C", str(path), "config", "user.name", "Schema Gate Test"),
    )
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, text=True)
    contract = path / "contract.txt"
    contract.write_text("pinned\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(path), "add", "contract.txt"),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ("git", "-C", str(path), "commit", "--quiet", "-m", "Pin contract"),
        check=True,
        capture_output=True,
        text=True,
    )
    revision = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    contract.write_text("uncommitted drift\n", encoding="utf-8")
    return revision


def test_required_ci_jobs_keep_project_environment_and_tool_contract() -> None:
    """Release and merge evidence must run with every required dependency."""
    ci = _yaml(REPO_ROOT / ".gitlab-ci.yml")
    assert "before_script" not in ci["publish-github"]
    assert any(
        "conda activate redfish_ctl" in command
        for command in ci["default"]["before_script"]
    )

    inventory = _yaml(REPO_ROOT / "inventory" / "ci" / "smoke-tests.yaml")
    records = {
        record["job"]: record
        for record in inventory["spec"]["smokeTests"]
    }
    merge_tools = {
        "gitleaks",
        "helm",
        "kubeconform",
        "kube-linter",
        "pytest",
        "python",
        "ruff",
        "shellcheck",
        "yq",
    }
    for job in ("project-ci-cpu-validation", "gate-merge"):
        assert merge_tools <= set(records[job]["requiredTools"])
    assert "builder-project-resolve-binding" in records["gate-integration"][
        "requiredTools"
    ]

    for job in ("gate-integration", "gate-scheduled"):
        rules = repr(ci[job].get("rules") or [])
        assert 'CI_COMMIT_REF_PROTECTED == "true"' in rules
        scheduled = [
            rule["if"]
            for rule in ci[job].get("rules") or []
            if isinstance(rule, dict) and "schedule" in str(rule.get("if", ""))
        ]
        assert len(scheduled) == 1
        assert "CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" in scheduled[0]


def test_project_token_gates_declare_the_credentials_they_consume() -> None:
    """Provider envelopes provision the exact project-token inputs used by gates."""
    manifest = _yaml(REPO_ROOT / "gates" / "manifest.yaml")
    expected = {"GITLAB_URL", "GITLAB_PROJECT_TOKEN", "GITLAB_PROJECT_ID"}
    records = {
        record["id"]: record
        for record in manifest["spec"]["gates"]
        if record["id"].startswith("gitlab.project-token.")
    }
    assert set(records) == {
        "gitlab.project-token.exists",
        "gitlab.project-token.project-bound",
        "gitlab.project-token.api-access",
        "gitlab.project-token.no-cross-project-access",
    }
    assert all(
        set(record["requiredCredentialNames"]) == expected
        for record in records.values()
    )


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
        yaml.safe_dump(
            {
                "spec": {
                    "source": {"revision": STANDARDS_REVISION},
                    "providers": [
                        {"name": "builder", "binding": "builder-binding.yaml"}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "builder-binding.yaml").write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "builder"},
                "spec": {
                    "dispatch": {"baseUrl": "https://ci.example.invalid"}
                },
            }
        ),
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
        "output_sanitized": True,
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
            "output_sanitized": True,
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
    """Return an environment outside Kubernetes and GitLab job authority."""
    return {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CI_PROJECT_NAME",
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
        expected_surface = (
            "protected-kubernetes" if record["mutates"] else "internal-gitlab"
        )
        assert indexed["executionSurface"] == expected_surface


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


def test_observe_gate_buffers_output_and_withholds_secret_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret-shaped gate output is scanned as a complete capture before printing."""
    root = tmp_path / "repo"
    root.mkdir()
    root = _root(root)
    ready = tmp_path / "gate-ready"
    release = tmp_path / "gate-release"
    command = root / "stream.sh"
    command.write_text(
        "#!/bin/sh\n"
        "printf 'first line\\n'\n"
        "printf 'ready\\n' >\"$GATE_READY_PATH\"\n"
        "while [ ! -e \"$GATE_RELEASE_PATH\" ]; do sleep 0.05; done\n"
        "printf 'password: hunter2hunter2\\n'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    monkeypatch.setenv("GATE_READY_PATH", str(ready))
    monkeypatch.setenv("GATE_RELEASE_PATH", str(release))
    output = io.StringIO()
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
        for _ in range(40):
            if ready.exists():
                break
            threading.Event().wait(0.05)
        assert ready.exists(), "gate command did not reach the mid-run marker"
        assert worker.is_alive(), "command finished before buffering was observed"
        assert output.getvalue() == ""
        release.write_text("continue\n", encoding="utf-8")
    finally:
        if not release.exists():
            release.write_text("continue\n", encoding="utf-8")
        worker.join(5)

    assert not worker.is_alive()
    observations = observed["result"]
    assert observations["return_code"] == 0
    assert observations["output_sanitized"] is False
    assert output.getvalue() == "gate output withheld: secret-shaped content detected\n"
    assert "hunter2hunter2" not in output.getvalue()
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
    assert evidence["status"] == "failed"
    assert evidence["return_code"] == 1
    assert evidence["evidence_sanitized"] is False


@pytest.mark.parametrize(
    "diagnostic",
    [
        "warning: fallback path used\n",
        "tests/example.py:1: UserWarning: fallback path used\n",
    ],
)
def test_observe_gate_counts_nonnumeric_warning_forms_as_non_green(
    tmp_path: Path,
    diagnostic: str,
) -> None:
    """Warning diagnostics without a numeric summary still fail green evidence."""
    root = _root(tmp_path)
    command = root / "warning-output.py"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({diagnostic!r})\n",
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

    assert observations["warnings"] >= 1
    assert evidence["status"] == "failed"
    assert evidence["return_code"] == 1


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
    root = tmp_path / "repo"
    root.mkdir()
    root = _root(root)
    probe_log = tmp_path / "probe.log"
    scripts = root / "scripts"
    scripts.mkdir()
    check = scripts / "check.sh"
    check.write_text(
        "#!/bin/sh\n"
        "command -v python3 >/dev/null 2>&1 || { "
        "printf 'python3 missing\\n' >&2; exit 1; }\n"
        "if [ -n \"${PROBE_LOG:-}\" ]; then\n"
        "  printf '%s\\n' \"$CI_JOB_NAME\" >\"$PROBE_LOG\"\n"
        "  printf '%s\\n' \"$@\" >>\"$PROBE_LOG\"\n"
        "fi\n"
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
    env = _ci_env_for(root)
    env["PROBE_LOG"] = str(probe_log)
    smoke_observations = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=env,
        root=root,
    )

    assert probe_log.read_text(encoding="utf-8").splitlines() == [
        "gate-merge-probe",
        "--profile",
        "merge",
    ]
    assert smoke_timeout_seconds(record) == 1800
    assert smoke_observations["bounded_timeout"] is True
    assert smoke_observations["protected_surface"] is True
    assert smoke_observations["sources"]["protected_surface"] == (
        "not applicable: smoke class wiring has no protected live surface"
    )
    gate_observations["applied_timeout_seconds"] = [1800.1]
    mismatched = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=_ci_env_for(root),
        root=root,
    )
    assert mismatched["bounded_timeout"] is False

    protected_env = _ci_env_for(root, job="gate-integration")
    protected_env.update(
        {
            "CI_SERVER_HOST": "ci.example.invalid",
            "CI_COMMIT_REF_PROTECTED": "true",
        }
    )
    protected = observe_smoke(
        record={**record, "job": "gate-integration", "class": "protected-live"},
        gate_observations={
            **_gate_observations(),
            "timeout_seconds": 1800,
            "applied_timeout_seconds": [1800],
        },
        env=protected_env,
        root=root,
    )
    assert protected["protected_surface"] is True
    assert protected["sources"]["protected_surface"] == (
        "Internal GitLab host and protected-ref environment assertion"
    )
    with pytest.raises(EvidenceError, match="timeoutSeconds"):
        smoke_timeout_seconds({**record, "timeoutSeconds": 0})


def test_observe_smoke_ignores_reports_but_fails_new_untracked_residue(
    tmp_path: Path,
) -> None:
    """The idempotency probe permits reports/ output but fails other new residue."""
    root = tmp_path / "repo"
    root.mkdir()
    root = _root(root)
    scripts = root / "scripts"
    scripts.mkdir()
    check = scripts / "check.sh"
    check.write_text(
        "#!/bin/sh\n"
        "command -v python3 >/dev/null 2>&1 || { "
        "printf 'python3 missing\\n' >&2; exit 1; }\n"
        "mkdir -p reports/smoke\n"
        "printf '{}\\n' >reports/smoke/project-ci-cpu-validation.json\n"
        "if [ \"${CREATE_RESIDUE:-}\" = yes ]; then "
        "printf 'dirty\\n' >untracked-residue.txt; fi\n"
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
    gate_observations = {
        **_gate_observations(),
        "timeout_seconds": 1800,
        "applied_timeout_seconds": [1800],
    }

    clean = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=_ci_env_for(root),
        root=root,
    )
    assert clean["idempotent_noop"] is True
    assert clean["cleanup_status"] == "passed"
    assert clean["remaining"] == []

    dirty_env = _ci_env_for(root)
    dirty_env["CREATE_RESIDUE"] = "yes"
    dirty = observe_smoke(
        record=record,
        gate_observations=gate_observations,
        env=dirty_env,
        root=root,
    )
    evidence = build_smoke_evidence(
        record=record,
        gate_observations=gate_observations,
        smoke_observations=dirty,
        env=dirty_env,
        root=root,
    )

    assert dirty["idempotent_noop"] is False
    assert dirty["cleanup_status"] == "failed"
    assert dirty["remaining"] == ["?? untracked-residue.txt"]
    assert evidence["status"] == "failed"
    assert evidence["checks"]["idempotent_noop"]["status"] == "failed"


def test_observe_smoke_probe_timeout_is_bounded_and_reaps_child(
    tmp_path: Path,
) -> None:
    """A hung exact-command smoke probe is bounded and does not leak children."""
    root = tmp_path / "repo"
    root.mkdir()
    root = _root(root)
    child_pid_path = tmp_path / "probe-child.pid"
    child_script = tmp_path / "probe_child.py"
    child_script.write_text(
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    check = scripts / "check.sh"
    check.write_text(
        "#!/bin/sh\n"
        "command -v python3 >/dev/null 2>&1 || {\n"
        "  printf 'python3 missing\\n' >&2\n"
        "  exit 1\n"
        "}\n"
        'python3 "$PROBE_CHILD_SCRIPT" "$PROBE_CHILD_PID" &\n'
        "sleep 5\n",
        encoding="utf-8",
    )
    check.chmod(0o755)
    record = {
        "job": "gate-merge",
        "class": "wiring",
        "command": "./scripts/check.sh --profile merge",
        "requiredTools": ["sh"],
        "artifactUnderTest": {"type": "repository", "digestSource": "git-commit"},
        "timeoutSeconds": 1,
    }
    gate_observations = {
        **_gate_observations(),
        "timeout_seconds": 1,
        "applied_timeout_seconds": [1],
    }
    env = _ci_env_for(root)
    env["PROBE_CHILD_PID"] = str(child_pid_path)
    env["PROBE_CHILD_SCRIPT"] = str(child_script)
    child_pid: int | None = None
    child_running = False
    start = time.monotonic()
    try:
        smoke_observations = observe_smoke(
            record=record,
            gate_observations=gate_observations,
            env=env,
            root=root,
        )
        elapsed = time.monotonic() - start
        if child_pid_path.exists():
            child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_running = False
            else:
                child_running = True
    finally:
        if child_pid is None and child_pid_path.exists():
            child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    assert elapsed < 3
    assert smoke_observations["idempotent_noop"] is False
    assert smoke_observations["cleanup_status"] == "passed"
    assert smoke_observations["remaining"] == []
    assert child_pid is not None
    assert child_running is False


@pytest.mark.parametrize("timeout", [59, 3601, True, None])
def test_provider_gate_timeout_rejects_out_of_contract_values(timeout) -> None:
    """A provider gate cannot escape the registered per-gate timeout bounds."""
    with pytest.raises(EvidenceError, match="timeoutSeconds"):
        gate_timeout_seconds({"timeoutSeconds": timeout})


def test_provider_gate_timeout_accepts_schema_bounds() -> None:
    """The execution adapter accepts both documented timeout boundaries."""
    assert gate_timeout_seconds({"timeoutSeconds": 60}) == 60
    assert gate_timeout_seconds({"timeoutSeconds": 3600}) == 3600


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


def test_required_jobs_have_local_artifacts_or_external_contracts() -> None:
    """Required jobs have local artifacts or one exact provider contract."""
    registry = _yaml(REPO_ROOT / "gates/manifest.yaml")
    ci = _yaml(REPO_ROOT / ".gitlab-ci.yml")
    smoke = _yaml(REPO_ROOT / "inventory/ci/smoke-tests.yaml")
    smoke_jobs = {record["job"] for record in smoke["spec"]["smokeTests"]}
    external_jobs = {
        job["name"]: job
        for include in registry["trusted_includes"]
        for job in include.get("jobs", [])
    }
    assert ci["gate-merge"]["script"] == [
        "./scripts/check.sh --list",
        "./scripts/check.sh --profile merge",
    ]
    for job_name in registry["required_jobs"]:
        assert job_name in smoke_jobs
        if job_name in external_jobs:
            assert job_name not in ci
            assert external_jobs[job_name]["required"] is True
            assert external_jobs[job_name]["allowFailure"] is False
            continue
        assert job_name in ci
        paths = _artifact_paths(ci[job_name])
        assert f"reports/ci/{job_name}.json" in paths
        assert f"reports/smoke/{job_name}.json" in paths


def test_smoke_inventory_generator_matches_the_tracked_inventory() -> None:
    """The canonical inventory renderer cannot drift from its tracked output."""
    proc = subprocess.run(
        [str(REPO_ROOT / "check.sh"), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "action=no-op" in proc.stdout


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
    assert "--project redfish_ctl" not in source
    assert "--host internal-gitlab" not in source
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


def test_dispatch_ref_is_explicit_safe_and_dispatch_only() -> None:
    """An immutable validation ref is bounded before Builder receives it."""
    source = CHECK_SH.read_text(encoding="utf-8")
    assert '--ref "$ref"' in source
    assert '--requested-commit "$commit"' in source

    proc = _run_check(
        ["--profile", "merge", "--ref", f"sync/pr-445/{COMMIT}"]
    )
    assert proc.returncode == 2
    assert "require --dispatch" in (proc.stdout + proc.stderr)

    unsafe = "sync/pr-445/bad ref\n" + ("x" * 260)
    proc = _run_check(
        ["--profile", "merge", "--dispatch", "--ref", unsafe]
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2
    assert unsafe not in combined
    assert "safe branch ref" in combined


def test_dispatch_forwards_immutable_ref_and_exact_commit(tmp_path: Path) -> None:
    """The Builder adapter receives the selected ref and current exact HEAD."""
    builder = tmp_path / "builder"
    builder_scripts = builder / "scripts"
    builder_scripts.mkdir(parents=True)
    project_ci = builder_scripts / "project-ci"
    project_ci.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n', encoding="utf-8"
    )
    project_ci.chmod(0o755)
    discovery = builder_scripts / "shared_inventory_map.sh"
    discovery.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' "
        "'{\"spec\":{\"providerCapabilities\":{\"capabilities\":["
        "{\"id\":\"ci.focused-gate\"},{\"id\":\"ci.merge-profile\"}]}}}'\n",
        encoding="utf-8",
    )
    discovery.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(builder)], check=True)
    subprocess.run(
        ["git", "-C", str(builder), "config", "user.email", "ci@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(builder), "config", "user.name", "CI Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(builder), "add", "scripts"], check=True)
    subprocess.run(
        ["git", "-C", str(builder), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    builder_revision = subprocess.check_output(
        ["git", "-C", str(builder), "rev-parse", "HEAD"], text=True
    ).strip()

    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copyfile(CHECK_SH, project / "scripts" / "check.sh")
    (project / "scripts" / "check.sh").chmod(0o755)
    (project / "builder-binding.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": {
                    "source": {
                        "localPath": str(builder),
                        "revision": builder_revision,
                    },
                    "discovery": {
                        "command": [
                            "./scripts/shared_inventory_map.sh",
                            "--validate",
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (project / "standards-binding.yaml").write_text(
        yaml.safe_dump({"metadata": {"name": "bound-project"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "ci@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "user.name", "CI Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "remote",
            "add",
            "origin",
            "https://example.invalid/runtime-project.git",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    project_commit = subprocess.check_output(
        ["git", "-C", str(project), "rev-parse", "HEAD"], text=True
    ).strip()
    validation_ref = f"sync/pr-445/{project_commit}"

    proc = subprocess.run(
        [
            str(project / "scripts" / "check.sh"),
            "--profile",
            "merge",
            "--dispatch",
            "--ref",
            validation_ref,
        ],
        cwd=project,
        env=_off_cluster_env(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    output = proc.stdout.splitlines()
    assert output[output.index("--project") + 1] == "bound-project"
    assert output[output.index("--ref") + 1] == validation_ref
    assert output[output.index("--requested-commit") + 1] == project_commit
    assert "--profile" in output
    assert "full" in output
    assert "--dry-run" in output

    mismatch = subprocess.run(
        [
            str(project / "scripts" / "check.sh"),
            "--profile",
            "merge",
            "--dispatch",
            "--ref",
            validation_ref,
        ],
        cwd=project,
        env={**_off_cluster_env(), "CI_PROJECT_NAME": "runtime-project"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert mismatch.returncode == 78
    assert "disagrees with standards-binding.yaml" in mismatch.stderr


def test_unit_profile_excludes_inapplicable_lanes_instead_of_skipping() -> None:
    """Required unit evidence deselects hardware, emulator, and schema lanes."""
    source = (REPO_ROOT / "scripts" / "gates" / "unit" / "all.sh").read_text(
        encoding="utf-8"
    )
    assert '-m "not live and not emulator_live and not dmtf_sim_live"' in source
    assert "--ignore=tests/gates/test_redfish_schema.py" in source


def test_unit_profile_clears_fixture_connection_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline gate clears connection inputs before it invokes pytest."""
    fixture_source = (REPO_ROOT / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )
    fixture_names = set(
        re.findall(r'os\.environ(?:\.get\(|\[)["\']([A-Z0-9_]+)', fixture_source)
    )
    connection_names = fixture_names | {
        "REDFISH_IP",
        "REDFISH_USERNAME",
        "REDFISH_PASSWORD",
        "REDFISH_PORT",
    }
    capture = tmp_path / "pytest-invocation.json"
    shim = tmp_path / "pytest"
    shim.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            from pathlib import Path
            import sys

            names = os.environ["UNIT_GATE_CONNECTION_NAMES"].split(",")
            present = sorted(name for name in names if name in os.environ)
            Path(os.environ["UNIT_GATE_CAPTURE"]).write_text(
                json.dumps({"args": sys.argv[1:], "present": present}),
                encoding="utf-8",
            )
            raise SystemExit(91 if present else 0)
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)

    assert fixture_names
    for name in connection_names:
        monkeypatch.setenv(name, "must-be-cleared")
    monkeypatch.setenv("UNIT_GATE_CONNECTION_NAMES", ",".join(connection_names))
    monkeypatch.setenv("UNIT_GATE_CAPTURE", str(capture))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "gates" / "unit" / "all.sh")],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["present"] == []
    assert observed["args"] == [
        "-q",
        "-ra",
        "-W",
        "error",
        "-m",
        "not live and not emulator_live and not dmtf_sim_live",
        "--ignore=tests/gates/test_redfish_schema.py",
    ]


@pytest.mark.parametrize(
    "path",
    ["tests/test_emulator_smoke.py", "tests/test_hpe_canary.py"],
)
def test_emulator_lanes_use_their_endpoint_specific_marker(path: str) -> None:
    """Emulator commands must not depend on the hardware fixture marker."""
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert "pytest.mark.emulator_live" in source
    assert "pytest.mark.live" not in source


def test_schema_gate_owns_exact_standards_and_provider_validation() -> None:
    """The schema gate uses local authorities or exact protected CI fetches."""
    source = (REPO_ROOT / "tools" / "schema_gate.py").read_text(encoding="utf-8")
    assert "project-standards-binding.schema.json" in source
    assert "project-provider-binding.schema.json" in source
    assert "_checkout_exact_authority" in source
    assert 'source.get("localPath"' in source
    assert 'source.get("repository"' in source
    assert "CI_JOB_TOKEN" in source

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
        "./tools/project-ci-cpu-validation.sh"
    )


def test_schema_gate_checks_out_the_binding_local_path_at_the_exact_revision(
    tmp_path: Path,
) -> None:
    """Local authority resolution ignores working-tree drift and uses the pin."""
    authority = tmp_path / "standards"
    revision = _commit_local_authority(authority)

    workspace = schema_gate._checkout_exact_local(str(authority), revision)
    try:
        assert (Path(workspace.name) / "contract.txt").read_text(
            encoding="utf-8"
        ) == "pinned\n"
    finally:
        workspace.cleanup()


def test_provider_coordinates_come_from_the_tracked_binding(tmp_path: Path) -> None:
    """Provider host and repository trust follow one tracked runtime route."""
    root = _root(tmp_path)
    assert provider_host(root) == "ci.example.invalid"
    assert require_provider_repository(
        "https://ci.example.invalid/group/contracts.git",
        "https://ci.example.invalid",
    ) == "https://ci.example.invalid/group/contracts.git"
    with pytest.raises(ProviderContractError, match="outside"):
        require_provider_repository(
            "https://elsewhere.example.invalid/group/contracts.git",
            "https://ci.example.invalid",
        )
