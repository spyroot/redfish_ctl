#!/usr/bin/env bash
# repo.mutation-policy (merge, mutates:false): destructive Redfish actions must
# stay classified DESTRUCTIVE or IRREVERSIBLE in action_policy. A power-off,
# password change, certificate replace, manager reset, or data erase can never
# be downgraded to REVERSIBLE/READ_ONLY, and the fail-safe default must hold.
# Implementation: tools/mutation_policy_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/mutation_policy_gate.py
