#!/usr/bin/env bash
# repo.shellcheck (merge, mutates:false): shellcheck the repo's shell scripts at error severity.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "repo.shellcheck: shellcheck not installed in this gate environment" >&2
  exit 1
fi
mapfile -t files < <(git ls-files 'scripts/*.sh' 'scripts/gates/**/*.sh' 'docker/**/*.sh')
# The repo tracks shell scripts under these pathspecs, so an empty set means the listing failed or the
# pathspecs went stale — not that there is nothing to check. Passing here would mean the gate proved
# nothing, so it fails instead.
if [ "${#files[@]}" -eq 0 ]; then
  echo "repo.shellcheck: no shell scripts matched — the gate would check nothing" >&2
  exit 1
fi
shellcheck -S error "${files[@]}"
echo "repo.shellcheck: OK (${#files[@]} scripts)"
