#!/usr/bin/env bash
# repo.arg-consistency (merge, mutates:false): one CLI concept, one spelling — no
# --event-type AND --event_type. Prevents a new command introducing a second
# spelling of an existing flag. Implementation: tools/arg_consistency_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/arg_consistency_gate.py
