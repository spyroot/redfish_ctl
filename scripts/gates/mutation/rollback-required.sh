#!/usr/bin/env bash
# mutation.rollback-required (deploy): the module being applied must expose a rollback step. Requires
# MODULE with an executable modules/<MODULE>/scripts/rollback.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
mod="${MODULE:-}"
if [ -z "$mod" ] || [ ! -x "modules/$mod/scripts/rollback.sh" ]; then
  echo "mutation.rollback-required: modules/$mod/scripts/rollback.sh missing (set MODULE)" >&2; exit 1
fi
echo "mutation.rollback-required: OK ($mod)"
