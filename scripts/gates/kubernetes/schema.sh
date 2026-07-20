#!/usr/bin/env bash
# kubernetes.schema (merge): validate k8s manifests against the upstream API schemas. Requires
# kubeconform in the toolchain (a missing validator FAILS the gate).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v kubeconform >/dev/null 2>&1; then
  echo "kubernetes.schema: kubeconform not installed in this gate environment" >&2
  exit 1
fi
# Validate concrete manifests only: skip Helm templates ({{ ... }}) and manifests carrying __PLACEHOLDER__
# substitutions, both of which are not valid YAML/k8s until rendered. Both markers live in file CONTENT,
# so they are matched against the file, never against its name.
files="$(git ls-files 'k8s/**/*.yaml' 'charts/**/*.yaml' | while read -r f; do
  grep -qE '\{\{|__[A-Z0-9_]+__' "$f" || echo "$f"
done)"
if [ -z "$files" ]; then
  echo "kubernetes.schema: no concrete manifests selected — the gate would validate nothing" >&2
  exit 1
fi
echo "$files" | xargs kubeconform -ignore-missing-schemas -summary
echo "kubernetes.schema: OK ($(echo "$files" | wc -l | tr -d ' ') manifests)"
