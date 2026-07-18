#!/usr/bin/env python3
"""Reject AI-agent identities in tracked files and NEW commit messages.

Enforces the hard rule that agent identities never leak into public git surfaces: no agent-tool name
(codex, claude) and no specialist-agent name (the ``.codex/agents`` / ``.claude/agents`` roles) in
tracked file content or in commit messages added on top of the base branch. Backs both the
``repo.no-agent-names`` gate and the ``commit-msg`` hook.

Two surfaces are intentionally NOT scanned, because they must reference the identities by design:
the ignore-lists (``.gitignore`` / ``.dockerignore``), and this guard's own implementation/hook/test.
Historical commit messages already on the base branch are accepted (only ``BASE..HEAD`` is scanned).

    python3 tools/agent_name_guard.py --tracked                 # scan tracked file content
    python3 tools/agent_name_guard.py --range origin/main..HEAD # scan NEW commit messages
    python3 tools/agent_name_guard.py --message .git/COMMIT_EDITMSG  # scan one commit-msg file
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Agent instruction/artifact files that must NEVER be tracked in a repo that publishes to GitHub.
# On the internal GitLab side they live (committed, version-controlled) in the private context repo;
# this list is what the public mainline is gated against. Basenames match anywhere in the tree.
_AGENT_FILE_GLOBS = [
    "CLAUDE.md", "CLAUDE_*.md", "CLAUDE_PATCH.diff", "CLAUDE_REVIEW*",
    "AGENTS.md", "AGENTS.private.md", "AGENT_BOOTSTRAP.md", "AGENT_HANDOFF.md",
    "CODEX_HANDOFF.md", "CODEX_TASKS.md", "CODEX_*.md",
    "TEAM_GUIDE.md", "IMPROVEMENT_PLAN.md", "ROADMAP*.md", "COORDINATION.md",
    "FLASH_BRAIN.md", "*_BRIEF.md",
]
# Directory prefixes whose entire contents are agent/internal-only.
_AGENT_DIR_PREFIXES = (".codex/", ".claude/", ".agent-review/", ".internal/",
                       "docs/internal/", "inventory/")

# Agent-tool names (word-bounded) plus specialist-agent role names (either separator).
_IDENTITIES = [
    r"\bcodex\b",
    r"\bclaude\b",
    r"cli[-_]ux[-_]critic",
    r"docs[-_]lean[-_]critic",
    r"docs[-_]tutorial[-_]critic",
    r"go[-_]no[-_]go[-_]gate[-_]engineer",
    r"redfish[-_]roadmap[-_]researcher",
    r"redfish[-_]test[-_]engineer",
    r"unit[-_]test[-_]engineer",
]
PATTERN = re.compile("|".join(_IDENTITIES), re.IGNORECASE)

# Paths that carry the identities by necessity — excluded from the tracked-content scan.
_EXCLUDE = [
    ".gitignore",
    ".dockerignore",
    "tools/agent_name_guard.py",
    "scripts/hooks/commit-msg",
    "scripts/gates/repository/no-agent-names.sh",
    "scripts/gates/repository/no-agent-files.sh",
    "tests/gates/test_no_agent_names.py",
    "tests/gates/test_no_agent_files.py",
]


def scan_text(text: str) -> list[str]:
    """Return every agent-identity match in a block of text.

    :param text: arbitrary text (a commit message, or a file's content).
    :return: the list of matched substrings (empty when clean).
    """
    return PATTERN.findall(text)


def _tracked_findings() -> list[str]:
    """Scan tracked file content for agent identities via ``git grep``.

    :return: ``file:line: text`` findings from tracked files, excluding the by-design paths.
    """
    excludes = [f":(exclude){p}" for p in _EXCLUDE]
    cmd = ["git", "grep", "-nIiE", PATTERN.pattern, "--", ".", *excludes]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def _range_findings(rng: str) -> list[str]:
    """Scan commit messages in a ``BASE..HEAD`` range for agent identities.

    :param rng: a git revision range (e.g. ``origin/main..HEAD``).
    :return: ``sha: subject`` findings; empty when the range is empty or unresolvable.
    """
    res = subprocess.run(
        ["git", "log", "--pretty=%H%x00%B%x00", rng],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if res.returncode != 0:
        return []
    findings = []
    for chunk in res.stdout.split("\x00\n"):
        if "\x00" not in chunk:
            continue
        sha, body = chunk.split("\x00", 1)
        if scan_text(body):
            findings.append(f"{sha[:12]}: {body.splitlines()[0] if body.strip() else ''}")
    return findings


def is_agent_file(path: str) -> bool:
    """Return whether a repo-relative path is an agent instruction/artifact file.

    :param path: a repo-relative file path (forward slashes).
    :return: True if it matches an agent-file glob or lives under an agent-only directory.
    """
    if path.startswith(_AGENT_DIR_PREFIXES):
        return True
    base = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(base, g) for g in _AGENT_FILE_GLOBS)


def _agent_file_findings() -> list[str]:
    """List tracked files that are agent instruction/artifact files.

    :return: the tracked agent-file paths (empty when the mainline is clean).
    """
    res = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True)
    return [p for p in res.stdout.splitlines() if p and is_agent_file(p)]


def main(argv: list[str] | None = None) -> int:
    """CLI entry: scan the requested surfaces and fail if any identity is found.

    :param argv: optional argument vector (defaults to ``sys.argv``).
    :return: 0 when clean, 1 when any agent identity is found.
    """
    ap = argparse.ArgumentParser(description="Reject agent identities/files in git surfaces.")
    ap.add_argument("--tracked", action="store_true", help="scan tracked file content for identities")
    ap.add_argument("--range", help="scan commit messages in BASE..HEAD for identities")
    ap.add_argument("--message", help="scan a single commit-message file for identities")
    ap.add_argument("--files", action="store_true", help="fail if any agent instruction/artifact file is tracked")
    args = ap.parse_args(argv)

    findings: list[str] = []
    if args.tracked:
        findings += _tracked_findings()
    if args.range:
        findings += _range_findings(args.range)
    if args.message:
        text = Path(args.message).read_text()
        if scan_text(text):
            findings.append(f"commit message: {text.splitlines()[0] if text.strip() else ''}")
    if args.files:
        findings += [f"tracked agent file: {p}" for p in _agent_file_findings()]

    if findings:
        sys.stderr.write("agent-name-guard: agent identity/file found in a git surface:\n")
        for f in findings:
            sys.stderr.write(f"  {f}\n")
        sys.stderr.write("Keep agent files in the private context repo; neutralize identities "
                         "(agent/runner/automation) before publishing to GitHub.\n")
        return 1
    print("agent-name-guard: OK — no agent identities/files in scanned surfaces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
