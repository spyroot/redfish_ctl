#!/usr/bin/env bash
# Thin simulator lifecycle adapter. Mutating stages default to --dry-run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/dcloud_simulator.py apply "$@"
