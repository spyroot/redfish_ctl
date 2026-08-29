"""Focused tests for the Builder CPU resource-job consumer adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "tools" / "project-ci-cpu-validation.sh"


def _resolve_mode(focused_gate: str, project_profile: str) -> subprocess.CompletedProcess[str]:
    """Call only the sourceable selection core without executing project gates."""
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; project_ci_cpu_mode "$2" "$3"',
            "project-ci-cpu-validation-test",
            str(ADAPTER),
            focused_gate,
            project_profile,
        ],
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


def test_focused_profile_without_gate_fails_closed() -> None:
    result = _resolve_mode("", "focused")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "requires FOCUSED_GATE" in result.stderr


def test_missing_profile_and_gate_fail_closed() -> None:
    result = _resolve_mode("", "")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "must be focused or full" in result.stderr
