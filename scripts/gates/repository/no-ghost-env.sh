#!/usr/bin/env bash
# repo.no-ghost-env (merge, mutates:false): every env var the code reads must be
# declared in tools/env_registry.txt. Stops agents inventing ghost env vars or
# resurrecting legacy names. Implementation: tools/no_ghost_env_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/no_ghost_env_gate.py
