#!/usr/bin/env bash
# repo.meta (merge, mutates:false): the meta-gate — gates.yaml + pipeline consistency.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
exec python tools/gate_meta.py
