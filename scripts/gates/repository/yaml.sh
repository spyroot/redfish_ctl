#!/usr/bin/env bash
# repo.yaml (merge): lint YAML with the tracked structural policy.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v yamllint >/dev/null 2>&1; then
  echo "repo.yaml: required command is unavailable: yamllint" >&2
  exit 1
fi
git ls-files '*.yml' '*.yaml' | grep -v '__' | grep -vE 'charts/[^/]+/templates/' | \
  xargs -r yamllint -c .yamllint
echo "repo.yaml: OK (yamllint)"
