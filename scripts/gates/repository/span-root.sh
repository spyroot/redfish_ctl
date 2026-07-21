#!/usr/bin/env bash
# repo.span-root (merge, mutates:false): every BMC HTTP call sits under a tracing
# span (client_span, or a partial handed to traced_request/traced_request_callable)
# so one operation renders as a single connected trace with no orphaned call.
# Implementation: tools/span_root_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/span_root_gate.py
