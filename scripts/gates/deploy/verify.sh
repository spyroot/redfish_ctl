#!/usr/bin/env bash
# deploy.verify (deploy, mutates:true): post-apply verification. A live apply MUST be followed by a
# module verify (and have a rollback available). Invoke with MODULE=<name>; it runs that module's
# verify.sh. Fails loudly if pointed at a module without an executable verify — never a no-op pass.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
mod="${MODULE:-}"
if [ -z "$mod" ] || [ ! -x "modules/$mod/scripts/verify.sh" ]; then
  echo "deploy.verify: set MODULE=<name> with an executable modules/<name>/scripts/verify.sh" >&2
  exit 2
fi
if [ ! -x "modules/$mod/scripts/rollback.sh" ]; then
  echo "deploy.verify: modules/$mod has no rollback.sh — an apply without rollback is forbidden" >&2
  exit 2
fi
exec "modules/$mod/scripts/verify.sh"
