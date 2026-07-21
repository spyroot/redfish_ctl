#!/usr/bin/env bash
# repo.dry-run-contract (merge, mutates:false): every mutable operational script
# must accept --dry-run (TEAM_GUIDE scripts contract). Ratchet: existing debt is
# grandfathered in tools/dry_run_baseline.txt; new/newly-mutating scripts must
# conform, and the baseline may only shrink. Implementation: tools/dry_run_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/dry_run_gate.py
