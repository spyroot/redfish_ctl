#!/usr/bin/env bash
# repo.no-legacy-resurrection (merge, mutates:false): a retired name (registry
# `retired`) must never reappear, and app code must not read a deprecated IDRAC_*
# name unpaired with its canonical. Implementation: tools/no_legacy_resurrection_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/no_legacy_resurrection_gate.py
