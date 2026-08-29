#!/usr/bin/env bash
# kubernetes.render (merge, mutates:false): statically render + validate the k8s manifests and the
# Helm charts. YAML-parses every non-templated manifest, then requires Helm to
# lint strictly and render every chart. No cluster contact is made.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
source scripts/gates/kubernetes/dmtf_sim_values.bash

# YAML syntax gate over static (non-__PLACEHOLDER__) manifests.
python - <<'PY'
import sys, pathlib, yaml
bad = []
for path in list(pathlib.Path("k8s").rglob("*.yaml")) + list(pathlib.Path("charts").rglob("*.yaml")):
    text = path.read_text(encoding="utf-8")
    if "__" in text and any(t.isupper() for t in text.split("__")[1:2]):
        continue  # templated (e.g. k8s/ci/test-job.yaml with __JOB_NAME__) — validated by helm/subst
    if "{{" in text:
        continue  # Helm template — validated by `helm template` below, not raw YAML
    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        bad.append(f"{path}: {exc}")
if bad:
    print("kubernetes.render: invalid YAML:\n" + "\n".join(bad), file=sys.stderr); sys.exit(1)
print("kubernetes.render: static manifests parse OK")
PY

if ! command -v helm >/dev/null 2>&1; then
  echo "kubernetes.render: helm not installed in this gate environment" >&2
  exit 1
fi

source_commit="${CI_COMMIT_SHA:-}"
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  source_commit="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "kubernetes.render: exact source commit is unavailable" >&2
  exit 1
fi

dmtf_sim_set_helm_values "$source_commit"

helm lint --strict charts/redfish-controller >/dev/null
helm template redfish-controller charts/redfish-controller \
  --namespace redfish-system >/dev/null
helm lint --strict charts/dmtf-sim \
  "${DMTF_SIM_HELM_VALUES[@]}" >/dev/null
helm template dmtf-sim charts/dmtf-sim \
  --namespace dmtf-bmc \
  --skip-tests \
  "${DMTF_SIM_HELM_VALUES[@]}" >/dev/null
helm template dmtf-sim charts/dmtf-sim \
  --namespace dmtf-bmc \
  --show-only templates/tests/test-connection.yaml \
  "${DMTF_SIM_HELM_VALUES[@]}" >/dev/null

if helm template dmtf-sim charts/dmtf-sim \
  --namespace dmtf-bmc \
  "${DMTF_SIM_HELM_VALUES[@]}" \
  --set-string dmtf.profile=no-such-profile >/dev/null 2>&1; then
  echo "kubernetes.render: dmtf-sim accepted an unknown profile" >&2
  exit 1
fi

echo "kubernetes.render: strict Helm lint + template OK"
