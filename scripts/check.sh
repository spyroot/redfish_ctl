#!/usr/bin/env bash
# check.sh — the single entry point for the gate registry (gates/manifest.yaml).
#
#   check.sh --list                 enumerate every registered gate (id, profile, mutates)
#   check.sh --profile <name>       run all mandatory gates of a profile (merge|integration|deploy)
#
# EXECUTION AUTHORITY = Kubernetes. Outside a cluster pod, check.sh REFUSES to run tests locally and
# prints the exact in-cluster dispatch command instead — it never runs a gate on the operator's laptop.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

_in_cluster() { [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; }

_list() {
  python - <<'PY'
import pathlib, yaml
reg = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text())
print(f"{'ID':30} {'PROFILE':12} {'MUTATES':8} COMMAND")
for g in reg["gates"]:
    print(f"{g['id']:30} {g['profile']:12} {str(g['mutates']):8} {g['command']}")
print(f"\n{len(reg['gates'])} gates; mandatory: {len(reg['mandatory_ids'])}; runner_tag: {reg['runner_tag']}")
PY
}

case "${1:-}" in
  --list)
    _list
    ;;
  --profile)
    profile="${2:?usage: check.sh --profile <merge|integration|deploy>}"
    if _in_cluster; then
      exec ./scripts/gates/run.sh "$profile"
    fi
    ref="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    echo "check.sh: REFUSING to run gates locally — Kubernetes is the execution authority." >&2
    echo "  Dispatch in-cluster instead:  make k8s-ci REF=$ref" >&2
    echo "  (or run inside a homelab-k8s runner/Job where \$KUBERNETES_SERVICE_HOST is set)" >&2
    exit 3
    ;;
  *)
    echo "usage: check.sh {--list | --profile <merge|integration|deploy>}" >&2
    exit 2
    ;;
esac
