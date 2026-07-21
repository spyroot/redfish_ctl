#!/usr/bin/env bash
# repo.no-local-build (merge, mutates:false): no committed target/script builds a
# docker image or mutates a cluster on a laptop — the toolbox pipeline does that
# (ci-toolbox.md "no ghosts"). Ratchet via tools/no_local_build_baseline.txt.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/no_local_build_gate.py
