#!/usr/bin/env bash
# repo.schemas (merge): validate tracked project bindings and local contracts
# against schemas fetched from their exact pinned Standards/Builder revisions.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/schema_gate.py
