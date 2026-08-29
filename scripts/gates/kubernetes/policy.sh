#!/usr/bin/env bash
# kubernetes.policy (merge): security/best-practice policy checks on the manifests. Requires kube-linter
# in the toolchain (a missing policy engine FAILS the gate — never an implicit pass).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v kube-linter >/dev/null 2>&1; then
  echo "kubernetes.policy: kube-linter not installed in this gate environment" >&2
  exit 1
fi
if ! command -v helm >/dev/null 2>&1; then
  echo "kubernetes.policy: helm not installed in this gate environment" >&2
  exit 1
fi
source_commit="${CI_COMMIT_SHA:-}"
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  source_commit="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "kubernetes.policy: exact source commit is unavailable" >&2
  exit 1
fi
rendered_dmtf="$(mktemp)"
trap 'rm -f -- "$rendered_dmtf"' EXIT
helm template dmtf-sim charts/dmtf-sim \
  --namespace dmtf-bmc \
  --set-string provenance.sourceCommit="$source_commit" >"$rendered_dmtf"
kube-linter lint \
  k8s/ \
  charts/redfish-controller/ \
  "$rendered_dmtf" && echo "kubernetes.policy: OK"
