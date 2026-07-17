#!/usr/bin/env bash
# repo.pytest (merge, mutates:false): the offline unit suite.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec pytest -q
