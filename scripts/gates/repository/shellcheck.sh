#!/usr/bin/env bash
# repo.shellcheck (merge, mutates:false): shellcheck the repo's shell scripts at error severity.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "repo.shellcheck: shellcheck not installed in this gate environment" >&2
  exit 1
fi
mapfile -t files < <(git ls-files 'scripts/*.sh' 'scripts/gates/**/*.sh' 'docker/**/*.sh' 2>/dev/null || true)
if [ "${#files[@]}" -eq 0 ]; then echo "repo.shellcheck: no shell scripts"; exit 0; fi
shellcheck -S error "${files[@]}"
echo "repo.shellcheck: OK"
