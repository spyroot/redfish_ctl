#!/usr/bin/env bash
# Drive the in-cluster redfish_ctl gate on the home Kubernetes cluster, the k8s
# analogue of scripts/gb300.sh: nothing runs on the operator workstation — the
# gate executes as a Job on the cluster nodes and this script only applies it,
# streams its logs, and returns its result. Uses the caller's current kubectl
# context (set it with `kubectl config use-context <home-cluster>` first).
#
#   k8s_ci.sh run   [<ref>] [<namespace>]   -> apply the gate Job, stream, exit code = result
#   k8s_ci.sh check                          -> cluster/node health table
#   k8s_ci.sh clean [<namespace>]            -> delete finished CI Jobs
#
# REF defaults to main and must be pushed to origin (the Job clones from GitHub).
set -euo pipefail

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_template="$_repo_root/k8s/ci/test-job.yaml"
NAMESPACE_DEFAULT="rfctl-ci"
# Pin the cluster: KUBE_CONTEXT (default home-lab-k8s) is asserted before any
# apply so an agent on the wrong context can never mutate the wrong cluster.
KUBE_CONTEXT="${KUBE_CONTEXT:-home-lab-k8s}"

_kubectl() { kubectl --context "$KUBE_CONTEXT" "$@"; }

# Fail CLOSED: an unreachable cluster or a missing context is a BLOCKER, never a
# silent pass. Callers gate every action behind this.
_require_reachable() {
    if ! kubectl config get-contexts -o name 2>/dev/null | grep -qx "$KUBE_CONTEXT"; then
        echo "BLOCKER: kubectl context '$KUBE_CONTEXT' not found." >&2
        echo "  ATTEMPTED: kubectl config get-contexts" >&2
        echo "  SAFE_NEXT_STEP: set KUBE_CONTEXT or fix ~/.kube/config; do NOT run tests locally." >&2
        exit 3
    fi
    if ! _kubectl cluster-info >/dev/null 2>&1; then
        echo "BLOCKER: home k8s cluster ('$KUBE_CONTEXT') is unreachable." >&2
        echo "  ATTEMPTED: kubectl --context $KUBE_CONTEXT cluster-info" >&2
        echo "  SAFE_NEXT_STEP: check VPN/kubeconfig; if the cluster is down, report the BLOCKER" >&2
        echo "  and STOP — do NOT run pytest/ruff/kind locally, do NOT hand-apply manifests." >&2
        exit 3
    fi
}

_ensure_ns() {
    local ns="$1"
    _kubectl get namespace "$ns" >/dev/null 2>&1 || _kubectl create namespace "$ns" >/dev/null
}

_slug() {
    # Bounded, DNS-safe label from a ref name.
    printf '%s' "$1" | tr '[:upper:]/_.' '[:lower:]---' | tr -cd 'a-z0-9-' | cut -c1-32
}

cmd="${1:-}"
case "$cmd" in
    run)
        ref="${2:-main}"
        ns="${3:-$NAMESPACE_DEFAULT}"
        case "$ref" in
            *[!A-Za-z0-9._/-]*|"") echo "k8s_ci: invalid ref '$ref'" >&2; exit 2 ;;
        esac
        _require_reachable
        _ensure_ns "$ns"
        slug="$(_slug "$ref")"
        # A per-run suffix keeps concurrent runs isolated (Bash RANDOM is fine here;
        # the workflow-runtime Date/Random ban does not apply to a plain shell).
        job="rfctl-ci-${slug}-${RANDOM}"
        manifest="$(sed -e "s|__JOB_NAME__|$job|g" -e "s|__NAMESPACE__|$ns|g" \
                        -e "s|__REF__|$ref|g" -e "s|__REF_LABEL__|$slug|g" "$_template")"
        printf '%s\n' "$manifest" | _kubectl apply -f - >/dev/null
        echo "k8s-ci: applied Job $ns/$job (ref=$ref)"
        # Wait for the pod to start, then stream logs to completion.
        _kubectl wait --for=condition=ready pod -l "job-name=$job" -n "$ns" --timeout=180s 2>/dev/null || true
        _kubectl logs -f "job/$job" -n "$ns" 2>/dev/null || true
        # Resolve the terminal state from the Job status.
        for _ in $(seq 1 30); do
            if _kubectl get job "$job" -n "$ns" -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null | grep -q True; then
                echo "k8s-ci: PASSED ($ns/$job)"; exit 0
            fi
            if _kubectl get job "$job" -n "$ns" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null | grep -q True; then
                echo "k8s-ci: FAILED ($ns/$job)" >&2; exit 1
            fi
            sleep 4
        done
        echo "k8s-ci: TIMED OUT resolving Job status ($ns/$job)" >&2; exit 1
        ;;
    check)
        _require_reachable
        echo "=== context: $KUBE_CONTEXT ==="
        echo "=== nodes ==="
        _kubectl get nodes -o custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,ARCH:.status.nodeInfo.architecture,VERSION:.status.nodeInfo.kubeletVersion' 2>&1
        echo "=== redfish CRDs / namespaces ==="
        _kubectl get crd 2>/dev/null | grep -iE 'redfish' || echo "(no redfish CRDs)"
        _kubectl get ns 2>/dev/null | grep -iE 'rfctl|redfish|runner' || echo "(no rfctl namespaces)"
        echo "=== recent CI jobs ==="
        _kubectl get jobs -n "$NAMESPACE_DEFAULT" 2>/dev/null || echo "(namespace $NAMESPACE_DEFAULT not present yet)"
        ;;
    clean)
        ns="${2:-$NAMESPACE_DEFAULT}"
        _kubectl delete jobs -n "$ns" -l app.kubernetes.io/name=redfish-ctl-ci 2>/dev/null || true
        echo "k8s-ci: cleaned finished CI jobs in $ns"
        ;;
    *)
        echo "usage: k8s_ci.sh {run [<ref>] [<namespace>]|check|clean [<namespace>]}" >&2
        exit 2
        ;;
esac
