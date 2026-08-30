#!/usr/bin/env bash
# kubernetes.schema (merge): validate k8s manifests against the upstream API schemas. Requires
# kubeconform in the toolchain (a missing validator FAILS the gate).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
source scripts/gates/kubernetes/dmtf_sim_values.bash
source scripts/gates/kubernetes/chart_coordinates.bash
if ! command -v kubeconform >/dev/null 2>&1; then
  echo "kubernetes.schema: kubeconform not installed in this gate environment" >&2
  exit 1
fi
if ! command -v helm >/dev/null 2>&1; then
  echo "kubernetes.schema: helm not installed in this gate environment" >&2
  exit 1
fi
source_commit="${CI_COMMIT_SHA:-}"
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  source_commit="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "kubernetes.schema: exact source commit is unavailable" >&2
  exit 1
fi
dmtf_sim_set_helm_values "$source_commit"
controller_release="$(helm_chart_name charts/redfish-controller)"
controller_namespace="$(helm_render_namespace "$controller_release")"
simulator_release="$(helm_chart_name charts/dmtf-sim)"
simulator_namespace="$(helm_render_namespace "$simulator_release")"
# Validate concrete manifests first. Skip Helm templates and placeholder
# manifests here; rendered chart output is validated separately below.
files="$(git ls-files \
  'k8s/*.yaml' 'k8s/**/*.yaml' \
  'charts/*.yaml' 'charts/**/*.yaml' | while read -r f; do
  case "$(basename "$f")" in Chart.yaml | values.yaml) continue ;; esac
  grep -qE '\{\{|__[A-Z0-9_]+__' "$f" || echo "$f"
done)"
if [ -z "$files" ]; then
  echo "kubernetes.schema: no concrete manifests selected — the gate would validate nothing" >&2
  exit 1
fi
echo "$files" | xargs kubeconform -ignore-missing-schemas -summary
helm template "$controller_release" charts/redfish-controller \
  --namespace "$controller_namespace" |
  kubeconform -ignore-missing-schemas -summary
helm template "$simulator_release" charts/dmtf-sim \
  --namespace "$simulator_namespace" \
  --skip-tests \
  "${DMTF_SIM_HELM_VALUES[@]}" |
  kubeconform -ignore-missing-schemas -summary
helm template "$simulator_release" charts/dmtf-sim \
  --namespace "$simulator_namespace" \
  --show-only templates/tests/test-connection.yaml \
  "${DMTF_SIM_HELM_VALUES[@]}" |
  kubeconform -ignore-missing-schemas -summary
static_count="$(echo "$files" | wc -l | tr -d ' ')"
echo "kubernetes.schema: OK (${static_count} static manifests + rendered charts)"
