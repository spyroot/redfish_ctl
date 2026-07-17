#!/usr/bin/env bash
# repo.http_bypass (merge, mutates:false): no NEW direct-HTTP bypass of the traced seams
# (gate G3 / release blocker #6). Runs the guard test when present.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if [ -f tests/test_no_new_http_bypass.py ]; then
  exec pytest -q tests/test_no_new_http_bypass.py
fi
echo "repo.http_bypass: guard test not present on this ref yet — skipping"
