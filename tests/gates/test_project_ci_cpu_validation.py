"""Focused tests for the Builder CPU resource-job consumer adapter."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "tools" / "project-ci-cpu-validation.sh"
TOOLBOX_DIGEST = "sha256:" + ("a" * 64)
STANDARDS_REVISION = "c" * 40


def _resolve_mode(
    focused_gate: str,
    project_profile: str,
    project_smoke: str = "",
) -> subprocess.CompletedProcess[str]:
    """Call only the sourceable selection core without executing project gates."""
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; project_ci_cpu_mode "$2" "$3" "$4"',
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            focused_gate,
            project_profile,
            project_smoke,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _resolve_gate(
    focused_gate: str,
    project_profile: str,
    project_smoke: str = "",
) -> subprocess.CompletedProcess[str]:
    """Call only the sourceable gate resolver without executing project gates."""
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; project_ci_cpu_gate "$2" "$3" "$4"',
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            focused_gate,
            project_profile,
            project_smoke,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_adapter(
    *args: str,
    focused_gate: str = "",
    project_profile: str = "",
    project_smoke: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run only non-executing adapter controls with explicit selector inputs."""
    env = os.environ.copy()
    env["FOCUSED_GATE"] = focused_gate
    env["PROJECT_CI_PROFILE"] = project_profile
    env["PROJECT_CI_SMOKE"] = project_smoke
    return subprocess.run(
        [str(ADAPTER), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_focused_gate_selects_one_focused_execution() -> None:
    result = _resolve_mode("unit.all", "focused")

    assert result.returncode == 0
    assert result.stdout == "focused\n"
    assert result.stderr == ""


def test_focused_gate_is_compatible_with_legacy_empty_profile() -> None:
    result = _resolve_mode("unit.all", "")

    assert result.returncode == 0
    assert result.stdout == "focused\n"


def test_full_profile_selects_one_full_execution() -> None:
    result = _resolve_mode("", "full")

    assert result.returncode == 0
    assert result.stdout == "full\n"
    assert result.stderr == ""


def test_conflicting_focused_and_full_inputs_fail_closed() -> None:
    result = _resolve_mode("unit.all", "full")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "cannot be combined" in result.stderr


def test_invalid_focused_gate_identifier_fails_closed() -> None:
    result = _resolve_mode('unit.all"bad', "focused")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "bounded gate identifier" in result.stderr
    assert "Safe next step:" in result.stderr


def test_focused_profile_without_gate_defaults_to_unit_all() -> None:
    result = _resolve_mode("", "focused")
    gate = _resolve_gate("", "focused")

    assert result.returncode == 0
    assert result.stdout == "focused\n"
    assert result.stderr == ""
    assert gate.returncode == 0
    assert gate.stdout == "unit.all\n"
    assert gate.stderr == ""


def test_missing_profile_and_gate_fail_closed() -> None:
    result = _resolve_mode("", "")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "must be focused or full" in result.stderr


def test_help_documents_non_interactive_agent_contract() -> None:
    result = _run_adapter("--help")

    assert result.returncode == 0
    assert "Audience: both" in result.stdout
    for option in (
        "--dry-run",
        "--log-format",
        "--log-level",
        "--log-file",
        "--run-id",
    ):
        assert option in result.stdout
    assert result.stderr == ""


def test_dry_run_reports_default_focused_gate_without_execution() -> None:
    result = _run_adapter(
        "--dry-run",
        "--log-format",
        "json",
        "--run-id",
        "cpu-contract-1",
        project_profile="focused",
    )

    assert result.returncode == 0
    assert result.stdout == (
        '{"mode":"focused","gate":"unit.all","mutation":false,'
        '"runId":"cpu-contract-1","status":"planned","warningCount":0,'
        '"cleanupStatus":"not-required","evidencePath":""}\n'
    )
    assert result.stderr == ""


def test_unknown_option_fails_with_safe_next_step() -> None:
    result = _run_adapter("--unknown", project_profile="focused")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "BLOCKER:" in result.stderr
    assert "Safe next step:" in result.stderr


def test_dry_run_rejects_log_file_side_effect(tmp_path: Path) -> None:
    log_path = tmp_path / "adapter.log"

    result = _run_adapter(
        "--dry-run",
        "--log-file",
        str(log_path),
        project_profile="focused",
    )

    assert result.returncode == 2
    assert not log_path.exists()
    assert "cannot be combined with --dry-run" in result.stderr


def test_exact_cpu_smoke_selector_resolves_one_bounded_mode() -> None:
    result = _resolve_mode("", "", "project-ci-cpu-validation")

    assert result.returncode == 0
    assert result.stdout == "smoke\n"
    assert result.stderr == ""


def test_unknown_cpu_smoke_selector_fails_closed() -> None:
    result = _resolve_mode("", "", "other-smoke")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "unsupported PROJECT_CI_SMOKE" in result.stderr


def test_json_log_is_contract_complete_and_appended(tmp_path: Path) -> None:
    log_path = tmp_path / "adapter.jsonl"
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                'source "$1"; '
                'project_ci_parse_args --log-format json --log-level debug '
                '--log-file "$2" --run-id log-1; '
                'project_ci_log_selection focused unit.all 25'
            ),
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            str(log_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    event = json.loads(result.stderr)
    assert json.loads(log_path.read_text(encoding="utf-8")) == event
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", event["timestamp"])
    assert set(event) == {
        "timestamp",
        "level",
        "runId",
        "component",
        "operation",
        "mode",
        "event",
        "attempt",
        "resource",
        "result",
        "elapsedMs",
        "errorClass",
        "safeNextStep",
        "message",
    }
    assert event["level"] == "info"
    assert event["runId"] == "log-1"
    assert event["component"] == "project-ci-cpu-validation"
    assert event["operation"] == "validate"
    assert event["mode"] == "focused"
    assert event["event"] == "selection"
    assert event["attempt"] == 1
    assert event["resource"] == "gate:unit.all"
    assert event["result"] == "running"
    assert event["elapsedMs"] == 25
    assert event["errorClass"] == "none"
    assert event["safeNextStep"] == ""
    assert event["message"] == "selected project CI validation"


def test_json_blocker_stays_machine_readable() -> None:
    result = _run_adapter(
        "--log-format",
        "json",
        "--run-id",
        "log-error",
        "--unknown",
        project_profile="focused",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    event = json.loads(result.stderr)
    assert event["level"] == "error"
    assert event["runId"] == "log-error"
    assert event["event"] == "blocker"
    assert event["result"] == "blocked"
    assert event["errorClass"] == "usage-error"
    assert event["safeNextStep"]
    assert event["message"] == "unknown option: --unknown"
    assert event["authorityChecked"]
    assert event["observation"] == "unknown option: --unknown"
    assert event["risk"]


def test_log_level_filters_info_event(tmp_path: Path) -> None:
    log_path = tmp_path / "filtered.jsonl"
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                'source "$1"; '
                'project_ci_parse_args --log-format json --log-level warning '
                '--log-file "$2" --run-id log-filter; '
                'project_ci_log_selection focused unit.all 0'
            ),
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            str(log_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not log_path.exists()


def _run_gate_dependency(
    dependency: str,
    *,
    mode: str = "smoke",
    gate: str = "",
    log_format: str = "text",
    cwd: Path | None = None,
    job_name: str = "project-ci-cpu-validation",
) -> subprocess.CompletedProcess[str]:
    if cwd is not None:
        inventory = cwd / "inventory" / "ci" / "smoke-tests.yaml"
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(
            "spec:\n"
            "  smokeTests:\n"
            "    - job: project-ci-cpu-validation\n"
            "      releaseBlocking: true\n"
            "      evidencePath: reports/smoke/project-ci-cpu-validation.json\n",
            encoding="utf-8",
        )
    command = (
        'source "$1"; '
        'project_ci_parse_args --log-format "$2" --run-id gate-test; '
        'project_ci_run_gate "$3" "$4" "$5" 0'
    )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            command,
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            log_format,
            dependency,
            mode,
            gate,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "CI_JOB_NAME": job_name},
    )


def _nested_gate_repo(tmp_path: Path, gate_body: str) -> Path:
    """Create a tiny repo that still uses the real check.sh -> run.sh path."""
    root = tmp_path / "nested-repo"
    for path in (
        "scripts",
        "scripts/gates",
        "scripts/gates/unit",
        "tools",
        "gates",
        "inventory/ci",
        "schemas",
        "bin",
    ):
        (root / path).mkdir(parents=True, exist_ok=True)
    for source, destination in (
        (REPO_ROOT / "scripts" / "check.sh", root / "scripts" / "check.sh"),
        (
            REPO_ROOT / "scripts" / "gates" / "run.sh",
            root / "scripts" / "gates" / "run.sh",
        ),
        (REPO_ROOT / "tools" / "__init__.py", root / "tools" / "__init__.py"),
        (REPO_ROOT / "tools" / "ci_evidence.py", root / "tools" / "ci_evidence.py"),
        (
            REPO_ROOT / "tools" / "provider_contract.py",
            root / "tools" / "provider_contract.py",
        ),
        (
            REPO_ROOT / "schemas" / "ci-evidence.schema.json",
            root / "schemas" / "ci-evidence.schema.json",
        ),
        (
            REPO_ROOT / "schemas" / "smoke-evidence.schema.json",
            root / "schemas" / "smoke-evidence.schema.json",
        ),
    ):
        shutil.copy2(source, destination)
    gate = root / "scripts" / "gates" / "unit" / "nested-output.sh"
    gate.write_text(gate_body, encoding="utf-8")
    gate.chmod(0o755)
    (root / ".gitlab-ci.yml").write_text(
        f"default:\n  image: registry.example/toolbox@{TOOLBOX_DIGEST}\n",
        encoding="utf-8",
    )
    (root / "standards-binding.yaml").write_text(
        "metadata:\n"
        "  name: redfish_ctl\n"
        "spec:\n"
        "  source:\n"
        f"    revision: {STANDARDS_REVISION}\n"
        "  providers:\n"
        "    - name: builder\n"
        "      binding: builder-binding.yaml\n",
        encoding="utf-8",
    )
    (root / "builder-binding.yaml").write_text(
        "metadata:\n"
        "  name: builder\n"
        "spec:\n"
        "  dispatch:\n"
        "    baseUrl: https://ci.example.invalid\n",
        encoding="utf-8",
    )
    (root / "inventory" / "ci" / "smoke-tests.yaml").write_text(
        "spec:\n  smokeTests: []\n",
        encoding="utf-8",
    )
    (root / "gates" / "manifest.yaml").write_text(
        "apiVersion: homelab.embedings.ai/v1alpha1\n"
        "kind: GateRegistry\n"
        "runner_tag: homelab-k8s\n"
        "mandatory_ids:\n"
        "  - unit.all\n"
        "required_jobs: []\n"
        "trusted_includes: []\n"
        "gates:\n"
        "  - id: unit.all\n"
        "    profile: merge\n"
        "    command: ./scripts/gates/unit/nested-output.sh\n"
        "    mutates: false\n"
        "    required: true\n"
        "spec:\n"
        "  gates:\n"
        "    - id: unit.all\n"
        "      required: true\n"
        "      mutation: false\n"
        "      profiles: [merge]\n"
        "      status: active\n"
        "      output: reports/gates/unit.all.json\n"
        "      executionSurface: internal-gitlab\n"
        "      timeoutSeconds: 60\n",
        encoding="utf-8",
    )
    (root / "bin" / "grep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (root / "bin" / "grep").chmod(0o755)
    for executable in (
        root / "scripts" / "check.sh",
        root / "scripts" / "gates" / "run.sh",
    ):
        executable.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "ci@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "CI Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    return root


def _nested_ci_env(root: Path) -> dict[str, str]:
    """Return CI identity and synthetic kubelet evidence for the fixture repo."""
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    return {
        **os.environ,
        "CI_JOB_NAME": "project-ci-cpu-validation",
        "CI_JOB_ID": "41",
        "CI_PIPELINE_ID": "17",
        "CI_COMMIT_SHA": commit,
        "CI_JOB_IMAGE": f"registry.example/toolbox@{TOOLBOX_DIGEST}",
        "KUBERNETES_SERVICE_HOST": "10.0.0.1",
        "KUBERNETES_SERVICE_PORT": "443",
        "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        "PROJECT_CI_PROFILE": "focused",
        "FOCUSED_GATE": "",
        "PROJECT_CI_SMOKE": "",
    }


def test_adapter_entrypoint_nested_gate_warning_secret_stderr_is_non_green(
    tmp_path: Path,
) -> None:
    root = _nested_gate_repo(
        tmp_path,
        "#!/bin/sh\n"
        "printf 'warning: nested warning\\n' >&2\n"
        "printf 'password: hunter2hunter2\\n' >&2\n",
    )

    result = subprocess.run(
        [str(ADAPTER), "--run-id", "nested-warning-secret"],
        cwd=root,
        env=_nested_ci_env(root),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    gate_evidence = json.loads(
        (root / "reports" / "gates" / "unit.all.json").read_text(encoding="utf-8")
    )
    job_evidence = json.loads(
        (
            root / "reports" / "ci" / "project-ci-cpu-validation.json"
        ).read_text(encoding="utf-8")
    )

    assert result.returncode == 1
    assert "hunter2hunter2" not in combined
    assert "password:" not in combined
    assert "gate output withheld: secret-shaped content detected" in result.stdout
    assert "error_class=gate-failed" in result.stdout
    assert gate_evidence["command"] == "./scripts/gates/unit/nested-output.sh"
    assert gate_evidence["status"] == "failed"
    assert gate_evidence["return_code"] == 1
    assert gate_evidence["warnings"] >= 1
    assert gate_evidence["evidence_sanitized"] is False
    assert "hunter2hunter2" not in json.dumps(gate_evidence, sort_keys=True)
    assert job_evidence["command"] == "./scripts/check.sh --profile merge --gate unit.all"
    assert job_evidence["status"] == "failed"
    assert job_evidence["warnings"] >= 1
    assert job_evidence["evidence_sanitized"] is False


def test_gate_dependency_failure_returns_terminal_result() -> None:
    result = _run_gate_dependency("/bin/false", mode="focused", gate="unit.all")

    assert result.returncode == 1
    assert "status=failed" in result.stdout
    assert "error_class=gate-failed" in result.stdout
    assert "result=failed" in result.stderr
    assert "cleanup_status=passed" in result.stdout


def test_gate_dependency_success_returns_terminal_result(tmp_path: Path) -> None:
    dependency = tmp_path / "dependency.sh"
    dependency.write_text(
        "#!/bin/bash\n"
        "mkdir -p reports/smoke\n"
        "printf '{}\\n' >reports/smoke/project-ci-cpu-validation.json\n",
        encoding="utf-8",
    )
    dependency.chmod(0o755)
    result = _run_gate_dependency(str(dependency), mode="smoke", cwd=tmp_path)

    assert result.returncode == 0
    assert "mode=smoke" in result.stdout
    assert "status=passed" in result.stdout
    assert "cleanup_status=passed" in result.stdout
    assert (
        "evidence_path=reports/smoke/project-ci-cpu-validation.json "
        "error_class=none"
    ) in result.stdout


def test_gate_dependency_success_without_evidence_fails_closed(tmp_path: Path) -> None:
    result = _run_gate_dependency("/bin/true", mode="full", cwd=tmp_path)

    assert result.returncode == 2
    assert "status=failed" in result.stdout
    assert "evidence_path= error_class=evidence-missing" in result.stdout

    probe = _run_gate_dependency(
        "/bin/true",
        mode="full",
        cwd=tmp_path,
        job_name="project-ci-cpu-validation-probe",
    )
    assert probe.returncode == 0
    assert "status=passed" in probe.stdout
    assert "evidence_path= error_class=none" in probe.stdout


def test_dependency_warning_stderr_is_counted_but_not_replayed(
    tmp_path: Path,
) -> None:
    diagnostics = [
        "warning: private dependency detail",
        "tests/example.py:1: UserWarning: private dependency detail",
    ]
    for index, diagnostic in enumerate(diagnostics):
        dependency = tmp_path / f"dependency-{index}.sh"
        dependency.write_text(
            f"#!/bin/bash\nprintf '%s\\n' {diagnostic!r} >&2\n",
            encoding="utf-8",
        )
        dependency.chmod(0o755)

        result = _run_gate_dependency(str(dependency), mode="full")
        combined = result.stdout + result.stderr

        assert result.returncode == 2
        assert diagnostic not in combined
        assert "stderr-lines=1" in result.stderr
        assert "warning:1" in result.stderr
        assert "redaction=full" in result.stderr
        assert "warning_count=1" in result.stdout
        assert "status=failed" in result.stdout
        assert "error_class=dependency-stderr" in result.stdout


def test_json_terminal_result_classifies_dependency_failure() -> None:
    result = _run_gate_dependency(
        "/bin/false",
        mode="focused",
        gate="unit.all",
        log_format="json",
    )

    assert result.returncode == 1
    terminal = json.loads(result.stdout)
    events = [json.loads(line) for line in result.stderr.splitlines()]
    event = next(item for item in events if item["event"] == "terminal")
    assert terminal["status"] == "failed"
    assert terminal["errorClass"] == "gate-failed"
    assert terminal["cleanupStatus"] == "passed"
    assert event["event"] == "terminal"
    assert event["errorClass"] == "gate-failed"


def test_invalid_run_id_cannot_break_json_blocker() -> None:
    result = _run_adapter(
        "--log-format",
        "json",
        "--run-id",
        'bad"value\nnext',
        project_profile="focused",
    )

    assert result.returncode == 2
    event = json.loads(result.stderr)
    assert event["runId"] == "invalid"
    assert event["message"] == "--run-id must be a bounded identifier"


def test_term_signal_reaps_gate_and_reports_cleanup(tmp_path: Path) -> None:
    dependency = tmp_path / "blocking-dependency.sh"
    child_pid_path = tmp_path / "child.pid"
    dependency.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$$\" >\"$CHILD_PID_PATH\"\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    dependency.chmod(0o755)
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    env["CHILD_PID_PATH"] = str(child_pid_path)
    process = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            (
                'source "$1"; '
                'project_ci_parse_args --log-format json --run-id signal-test; '
                'project_ci_run_gate "$2" full "" 0'
            ),
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            str(dependency),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())

    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 143
    terminal = json.loads(stdout)
    assert terminal["status"] == "failed"
    assert terminal["errorClass"] == "signal-term"
    assert terminal["cleanupStatus"] == "passed"
    events = [json.loads(line) for line in stderr.splitlines()]
    assert any(event["event"] == "cleanup" for event in events)
    assert any(event["event"] == "signal" for event in events)
    assert not list(tmp_path.glob("project-ci-cpu-validation.*"))
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        child_running = False
    else:
        child_running = True
    assert not child_running
