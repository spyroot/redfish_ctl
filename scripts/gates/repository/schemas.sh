#!/usr/bin/env bash
# repo.schemas (merge): validate tracked project bindings and local contracts
# against local Standards/Builder authorities at their exact pinned revisions.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/schema_gate.py
