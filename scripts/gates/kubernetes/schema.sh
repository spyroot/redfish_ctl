#!/usr/bin/env bash
# kubernetes.schema (merge): validate k8s manifests against the upstream API schemas. Requires
# kubeconform in the toolchain (a missing validator FAILS the gate).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v kubeconform >/dev/null 2>&1; then
  echo "kubernetes.schema: kubeconform not installed in this gate environment" >&2
  exit 1
fi
files="$(git ls-files 'k8s/**/*.yaml' 'charts/**/*.yaml' | grep -v '__' | while read -r f; do grep -qL '{{' "$f" && echo "$f"; done)"
[ -n "$files" ] && echo "$files" | xargs kubeconform -ignore-missing-schemas -summary
echo "kubernetes.schema: OK"
