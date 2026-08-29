#!/usr/bin/env bash
# repo.rename-tombstone (merge, mutates:false): a renamed-away public name
# (idrac_ctl -> redfish_ctl) never reappears outside the baselined compat-alias
# surface; docs cleanups must shrink the baseline (ratchet to the alias alone).
# Implementation: tools/rename_tombstone_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/rename_tombstone_gate.py
