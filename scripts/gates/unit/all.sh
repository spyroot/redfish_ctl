#!/usr/bin/env bash
# unit.all (merge): the full offline unit suite.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec pytest -q
