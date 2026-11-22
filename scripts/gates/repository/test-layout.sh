#!/usr/bin/env bash
# repo.test-layout (merge, mutates:false): a test lives in the tests/<domain>/
# dir mirroring its redfish_ctl/<domain>/ subject; a NEW flat tests/test_*.py
# fails (flat is only for root-module/infra tests, grandfathered in the
# baseline). Keeps a test's vendor/command subject visible from its path so
# cross-vendor misassertions cannot hide in a flat pile.
# Implementation: tools/test_layout_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/test_layout_gate.py
