#!/usr/bin/env bash
# repo.shellcheck (merge, mutates:false): run the shared ShellCheck style baseline.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "repo.shellcheck: shellcheck not installed in this gate environment" >&2
  exit 1
fi
patterns=(
  'build_dist.sh'
  'check.sh'
  'scripts/*.sh'
  'scripts/*.bash'
  'tools/*.sh'
  'docker/*.sh'
)
files=()
for pathspec in "${patterns[@]}"; do
  mapfile -t matched < <(git ls-files "$pathspec")
  if [ "${#matched[@]}" -eq 0 ]; then
    echo "repo.shellcheck: tracked pathspec matched nothing: $pathspec" >&2
    exit 1
  fi
  files+=("${matched[@]}")
done
shellcheck -x -S style "${files[@]}"
echo "repo.shellcheck: OK (${#files[@]} scripts)"
