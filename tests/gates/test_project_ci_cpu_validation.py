"""Focused tests for the Builder CPU resource-job consumer adapter."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "tools" / "project-ci-cpu-validation.sh"


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
) -> subprocess.CompletedProcess[str]:
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
    )


def test_gate_dependency_failure_returns_terminal_result() -> None:
    result = _run_gate_dependency("/bin/false", mode="focused", gate="unit.all")

    assert result.returncode == 1
    assert "status=failed" in result.stdout
    assert "error_class=gate-failed" in result.stdout
    assert "result=failed" in result.stderr
    assert "cleanup_status=passed" in result.stdout


def test_gate_dependency_success_returns_terminal_result() -> None:
    result = _run_gate_dependency("/bin/true", mode="smoke")

    assert result.returncode == 0
    assert "mode=smoke" in result.stdout
    assert "status=passed" in result.stdout
    assert "cleanup_status=passed" in result.stdout
    assert "evidence_path= error_class=none" in result.stdout


def test_successful_dependency_yq_warning_is_sanitized(tmp_path: Path) -> None:
    dependency = tmp_path / "dependency.sh"
    dependency.write_text(
        "#!/bin/bash\n"
        "printf 'level=WARN msg=\"yq wrote advisory warning\"\\n' >&2\n",
        encoding="utf-8",
    )
    dependency.chmod(0o755)

    result = _run_gate_dependency(str(dependency), mode="full")
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "status=passed" in result.stdout
    assert "warning_count=1" in result.stdout
    assert "cleanup_status=passed" in result.stdout
    assert "error_class=none" in result.stdout
    assert "level=warning" in result.stderr
    assert "result=passed" in result.stderr
    assert "stderr-lines=1" in result.stderr
    assert "classes=auth:0,network:0,warning:1,error:0,other:0" in result.stderr
    assert "redaction=full" in result.stderr
    assert "level=WARN" not in combined
    assert "yq wrote advisory warning" not in combined


def test_dependency_stderr_is_counted_but_not_replayed(tmp_path: Path) -> None:
    dependency = tmp_path / "dependency.sh"
    dependency.write_text(
        "#!/bin/bash\nprintf 'private dependency detail\\n' >&2\n",
        encoding="utf-8",
    )
    dependency.chmod(0o755)

    result = _run_gate_dependency(str(dependency), mode="full")
    combined = result.stdout + result.stderr

    assert result.returncode == 2
    assert "private dependency detail" not in combined
    assert "stderr-lines=1" in result.stderr
    assert "classes=auth:0,network:0,warning:0,error:0,other:1" in result.stderr
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
