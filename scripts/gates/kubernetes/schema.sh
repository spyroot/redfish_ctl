#!/usr/bin/env bash
# kubernetes.schema (merge): validate k8s manifests against the upstream API schemas. Requires
# kubeconform in the toolchain (a missing validator FAILS the gate).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v kubeconform >/dev/null 2>&1; then
  echo "kubernetes.schema: kubeconform not installed in this gate environment" >&2
  exit 1
fi
# Validate concrete manifests only. Skip by CONTENT: Helm templates ({{ ... }}) and manifests carrying
# __PLACEHOLDER__ substitutions (not valid YAML/k8s until rendered). Skip by NAME: a Helm chart's
# Chart.yaml / values.yaml -- plain YAML but chart metadata, not k8s resources (no 'kind'), so
# kubeconform rejects them ("missing 'kind' key"). templates/ (rendered elsewhere) and crds/ still flow through.
files="$(git ls-files 'k8s/**/*.yaml' 'charts/**/*.yaml' | while read -r f; do
  case "$(basename "$f")" in Chart.yaml | values.yaml) continue ;; esac
  grep -qE '\{\{|__[A-Z0-9_]+__' "$f" || echo "$f"
done)"
if [ -z "$files" ]; then
  echo "kubernetes.schema: no concrete manifests selected — the gate would validate nothing" >&2
  exit 1
fi
echo "$files" | xargs kubeconform -ignore-missing-schemas -summary
echo "kubernetes.schema: OK ($(echo "$files" | wc -l | tr -d ' ') manifests)"
