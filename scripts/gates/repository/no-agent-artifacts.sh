#!/usr/bin/env bash
# repo.no-agent-artifacts (repository-export, mutates:false): no agent, handoff, coordination, or
# private operator artifact may be visible at the public boundary.
#
# Distinct from repo.no-agent-files, which guards the WORKING TREE during a merge pipeline. This one
# guards what the export actually ships: the tree at the exact commit being pushed. They can disagree
# — a file deleted from the tip is still present in an older commit, and an export pushes history, not
# a snapshot. The merge-time gate cannot see that; this one is the boundary's own check.
#
# Named by the shared contract (builder docs/external/gates.md) and required by the export chain, so the id is
# not ours to rename.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

# The deny-list lives in ONE place: tools/agent_name_guard.py. Re-listing patterns here would fork it,
# and a fork is how the two copies drift apart until one stops catching things.
exec python3 tools/agent_name_guard.py --files
