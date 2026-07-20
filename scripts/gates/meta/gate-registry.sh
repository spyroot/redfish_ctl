#!/usr/bin/env bash
# meta.gate-registry (merge): the registry is schema-valid, ids unique, every command exists+executable,
# every mandatory id present. Enforced by tools/gate_meta.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/gate_meta.py
