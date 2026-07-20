#!/usr/bin/env bash
# check.sh — the single entry point for the gate registry (gates/manifest.yaml).
#
#   check.sh --list                 enumerate every registered gate (id, profile, mutates)
#   check.sh --profile <name>       run all mandatory gates of a profile
#                                   (merge|integration|deploy|repository-export)
#
# EXECUTION AUTHORITY = Kubernetes. Outside a cluster pod, check.sh REFUSES to run tests locally and
# prints the exact in-cluster dispatch command instead — it never runs a gate on the operator's laptop.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Kubernetes is the execution authority, so this predicate is a safety guard, not a convenience.
# Deciding it from ONE environment variable is too weak: exporting KUBERNETES_SERVICE_HOST on a
# workstation would run the whole profile on the laptop. Three independent kinds of evidence are
# required instead:
#   1. BOTH master-service variables, which the kubelet injects together — never just one;
#   2. a readable /proc/1/cgroup — Linux process evidence that cannot exist on the operator's macOS;
#   3. at least one artifact only a kubelet produces (_kubelet_evidence below).
# There is deliberately NO override variable: an escape hatch is how a guard stops being a guard.
_kubelet_evidence() {
  # An OR on purpose. A pod with automountServiceAccountToken:false (platform/agent-runner/job.yaml)
  # has no service-account files, and a cgroup-v2 pod with a private cgroup namespace reads only
  # "0::/", so requiring any single one of these would refuse to run inside a legitimate Job.
  [ -r /var/run/secrets/kubernetes.io/serviceaccount/token ] ||
    [ -r /var/run/secrets/kubernetes.io/serviceaccount/namespace ] ||
    grep -qs 'svc\.cluster\.local' /etc/resolv.conf ||
    grep -qsE 'kubernetes\.io~|kubelet/pods' /proc/self/mountinfo ||
    grep -qs 'kubepods' /proc/1/cgroup
}

_in_cluster() {
  [ -n "${KUBERNETES_SERVICE_HOST:-}" ] &&
    [ -n "${KUBERNETES_SERVICE_PORT:-}" ] &&
    [ -r /proc/1/cgroup ] &&
    _kubelet_evidence
}

_list() {
  python3 - <<'PY'
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
    profile="${2:?usage: check.sh --profile <merge|integration|deploy|repository-export>}"
    if _in_cluster; then
      exec ./scripts/gates/run.sh "$profile"
    fi
    ref="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    echo "check.sh: REFUSING to run gates locally — Kubernetes is the execution authority." >&2
    echo "  Dispatch in-cluster instead: push $ref and let the GitLab pipeline run it on the" >&2
    echo "  homelab-k8s runner. There is no workstation dispatch path." >&2
    echo "  (or run inside a homelab-k8s runner/Job — a pod is detected from the kubelet's own" >&2
    echo "   evidence, not from an environment variable, so exporting one cannot bypass this)" >&2
    exit 3
    ;;
  *)
    echo "usage: check.sh {--list | --profile <merge|integration|deploy|repository-export>}" >&2
    exit 2
    ;;
esac
