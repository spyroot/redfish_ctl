#!/usr/bin/env bash
# Gate repo.no-agent-names: no AI-agent identity (codex/claude/specialist roles) in tracked file
# content or in commit messages added on top of the base branch. Backed by tools/agent_name_guard.py.
set -euo pipefail
cd "$(dirname "$0")/../../.."
BASE_REF="${BASE_REF:-origin/main}"
if git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  exec python3 tools/agent_name_guard.py --tracked --range "${BASE_REF}..HEAD"
fi
# Base ref not resolvable in this checkout — scan tracked content only.
exec python3 tools/agent_name_guard.py --tracked
