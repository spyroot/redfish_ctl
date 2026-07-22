#!/usr/bin/env bash
# repo.span-root (merge, mutates:false): static raw-HTTP analysis plus emitted
# parent/kind/link/attribute topology for CLI, controller, and fleet roots.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
python3 tools/span_root_gate.py
exec python3 -m pytest -q \
  tests/test_span_root_gate.py \
  tests/test_tracing.py \
  tests/test_k8s_controller.py::test_kopf_handler_wraps_poll_in_controller_span \
  tests/test_k8s_node_profile_controller.py::test_kopf_handler_wraps_node_profile_reconcile_in_controller_span
