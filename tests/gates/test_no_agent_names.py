"""Cover the agent-name guard that backs the repo.no-agent-names gate.

The forbidden identities are assembled at runtime (never written as literals here) so this test file
itself stays clean and is not flagged by the guard it exercises.
"""
from tools import agent_name_guard


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
