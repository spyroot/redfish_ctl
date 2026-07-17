#!/usr/bin/env bash
# mutation.verify-required (deploy): the module being applied must expose a verify step, so the result
# is checked after the apply. Requires MODULE with an executable modules/<MODULE>/scripts/verify.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
mod="${MODULE:-}"
if [ -z "$mod" ] || [ ! -x "modules/$mod/scripts/verify.sh" ]; then
  echo "mutation.verify-required: modules/$mod/scripts/verify.sh missing (set MODULE)" >&2; exit 1
fi
echo "mutation.verify-required: OK ($mod)"
