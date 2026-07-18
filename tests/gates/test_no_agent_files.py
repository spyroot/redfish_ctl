"""Cover the agent-FILE detector that backs the repo.no-agent-files gate.

The published mainline must carry no agent instruction/artifact file; those live (committed) in the
private context repo on the internal GitLab instead. The current tree is clean, so the live gate passes.
"""
from tools import agent_name_guard


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
    assert not agent_name_guard.is_agent_file("docs/gates.md")
    assert not agent_name_guard.is_agent_file("README.md")


def test_live_mainline_has_no_tracked_agent_files():
    """The published mainline currently tracks zero agent files (the gate passes here)."""
    assert agent_name_guard._agent_file_findings() == []
