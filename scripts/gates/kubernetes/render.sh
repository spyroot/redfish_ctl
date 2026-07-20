#!/usr/bin/env bash
# kubernetes.render (merge, mutates:false): statically render + validate the k8s manifests and the
# Helm chart. YAML-parses every non-templated manifest (always runnable) and, when helm is present,
# lints + templates the chart. No cluster contact — pure render/validate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

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

if command -v helm >/dev/null 2>&1; then
  helm lint charts/redfish-controller >/dev/null
  helm template charts/redfish-controller >/dev/null
  echo "kubernetes.render: helm lint + template OK"
else
  echo "kubernetes.render: helm not installed — chart render skipped"
fi
