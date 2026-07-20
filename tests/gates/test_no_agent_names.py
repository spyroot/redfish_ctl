"""Cover the agent-name guard that backs the repo.no-agent-names gate.

The forbidden identities are assembled at runtime (never written as literals here) so this test file
itself stays clean and is not flagged by the guard it exercises.
"""
import subprocess

import pytest

from tools import agent_name_guard


def _fake_git(returncode, stdout="", stderr="fatal: detected dubious ownership in repository"):
    """Build a ``subprocess.run`` stand-in that reports a fixed git exit code.

    :param returncode: the exit code the fake git reports.
    :param stdout: the stdout the fake git emits (empty, as a crashed git emits).
    :param stderr: the stderr the fake git emits.
    :return: a callable with the ``subprocess.run`` signature the guard uses.
    """
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_tracked_scan_raises_when_git_grep_errors(monkeypatch):
    """A git grep error (exit >1) raises instead of reading as a clean tree.

    Exit 128 is a real CI edge: a pod whose checkout UID differs from the job UID gets "dubious
    ownership", and the resulting empty stdout is otherwise indistinguishable from "no findings" —
    which would publish agent identities to the public mirror precisely when the scan failed.
    """
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(128))
    with pytest.raises(agent_name_guard.GitCommandError):
        agent_name_guard._tracked_findings()


def test_tracked_scan_treats_no_matches_as_clean(monkeypatch):
    """git grep exit 1 means "no matches" — the clean case — and must not raise.

    The inverse edge of the test above: hardening exit-code handling must not turn the ordinary
    clean result into a gate failure.
    """
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(1))
    assert agent_name_guard._tracked_findings() == []


def test_range_scan_raises_when_range_unresolvable(monkeypatch):
    """An unresolvable commit range fails the gate instead of reporting zero new commits."""
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(128))
    with pytest.raises(agent_name_guard.GitCommandError):
        agent_name_guard._range_findings("origin/main..HEAD")


def test_range_scan_accepts_empty_range(monkeypatch):
    """An empty range exits 0 with no output and is genuinely clean, not an error."""
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(0, stdout=""))
    assert agent_name_guard._range_findings("origin/main..HEAD") == []


def test_gate_does_not_pass_when_git_fails(monkeypatch):
    """The CLI exits non-zero when the scan could not run — the fail-open defect itself."""
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(128))
    assert agent_name_guard.main(["--tracked"]) != 0


def test_gate_refuses_to_pass_with_no_surface_selected():
    """Invoked with no surface flag the guard reports failure, never a vacuous OK.

    A gate that scans nothing and prints OK is the same defect in a smaller form.
    """
    assert agent_name_guard.main([]) != 0


def test_flags_agent_tool_name():
    """A commit-message-style string naming an agent tool is flagged."""
    tool = "co" + "dex"  # assembled so this test file carries no literal identity
    assert agent_name_guard.scan_text(f"Merge branch '{tool}/foo'")


def test_flags_specialist_role_name():
    """A specialist-agent role name (either separator) is flagged."""
    role = "unit" + "_test_engineer"
    assert agent_name_guard.scan_text(f"Found by the {role}")


def test_clean_text_passes():
    """Neutral automation wording produces no findings."""
    assert agent_name_guard.scan_text("agent-runner ran the repository-editing task") == []


def test_word_boundary_avoids_false_positive():
    """Unrelated words that merely contain 'code' do not match the word-bounded tool names."""
    assert agent_name_guard.scan_text("encoded payload in the codebase") == []
