"""Cover the agent-FILE detector that backs the repo.no-agent-files gate.

The published mainline must carry no agent instruction/artifact file; those live (committed) in the
private context repo on the internal GitLab instead. The current tree is clean, so the live gate passes.
"""
import subprocess

import pytest

from tools import agent_name_guard


def _fake_git(returncode, stdout="", stderr="fatal: not a git repository"):
    """Build a ``subprocess.run`` stand-in that reports a fixed git exit code.

    :param returncode: the exit code the fake git reports.
    :param stdout: the stdout the fake git emits.
    :param stderr: the stderr the fake git emits.
    :return: a callable with the ``subprocess.run`` signature the guard uses.
    """
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_file_scan_raises_when_ls_files_fails(monkeypatch):
    """A failing ``git ls-files`` raises instead of reporting an empty (clean) file list.

    ``git ls-files`` exits 0 even for an empty repo, so any non-zero exit means the listing did not
    happen — and an unchecked empty result would let the publish gate wave through a tree it never read.
    """
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(128))
    with pytest.raises(agent_name_guard.GitCommandError):
        agent_name_guard._agent_file_findings()


def test_files_gate_does_not_pass_when_git_fails(monkeypatch):
    """The --files gate exits non-zero when the listing could not run."""
    monkeypatch.setattr(agent_name_guard.subprocess, "run", _fake_git(128))
    assert agent_name_guard.main(["--files"]) != 0


def test_flags_agent_instruction_files():
    """Known agent instruction files are recognized wherever they sit in the tree."""
    assert agent_name_guard.is_agent_file("CLAUDE.md")
    assert agent_name_guard.is_agent_file("AGENTS.md")
    assert agent_name_guard.is_agent_file("sub/dir/TEAM_GUIDE.md")
    assert agent_name_guard.is_agent_file("nightly_BRIEF.md")


def test_flags_agent_only_directories():
    """Anything under an agent-only directory is an agent file."""
    assert agent_name_guard.is_agent_file(".codex/agents/x.toml")
    assert agent_name_guard.is_agent_file(".claude/agents/y.md")
    assert agent_name_guard.is_agent_file(".internal/SECRET_REGISTRY.md")
    assert agent_name_guard.is_agent_file("inventory/home-lab/cluster.yaml")


def test_ordinary_source_is_not_flagged():
    """Real project source and docs are not agent files."""
    assert not agent_name_guard.is_agent_file("redfish_ctl/redfish_manager.py")
    assert not agent_name_guard.is_agent_file("docs/external/gates.md")
    assert not agent_name_guard.is_agent_file("README.md")


def test_live_mainline_has_no_tracked_agent_files():
    """The published mainline currently tracks zero agent files (the gate passes here)."""
    assert agent_name_guard._agent_file_findings() == []
