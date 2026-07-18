#!/usr/bin/env bash
# Gate repo.no-agent-files: no agent instruction/artifact file (CLAUDE.md, AGENTS.md, .codex/, .claude/,
# .internal/, TEAM_GUIDE.md, …) may be TRACKED in the published mainline. Agent files live, committed,
# in the private context repo on the internal GitLab — never in the repo that publishes to GitHub.
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec python3 tools/agent_name_guard.py --files
