#!/usr/bin/env bash
# repo.live-mutation-roundtrip (merge, mutates:false): a live-marked test may
# mutate a BMC only through tests/live_utils.live_roundtrip, which proves
# capture -> set -> assert -> restore -> assert. A direct base_patch/base_post/
# base_delete/invoke_action call in a live test bypasses the restoration proof.
#
# Implementation is AST-based (tools/live_mutation_gate.py): docstring or
# comment mentions never trip it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/live_mutation_gate.py
