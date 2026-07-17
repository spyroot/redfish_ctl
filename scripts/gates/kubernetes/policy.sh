#!/usr/bin/env bash
# kubernetes.policy (merge): security/best-practice policy checks on the manifests. Requires kube-linter
# in the toolchain (a missing policy engine FAILS the gate — never an implicit pass).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v kube-linter >/dev/null 2>&1; then
  echo "kubernetes.policy: kube-linter not installed in this gate environment" >&2
  exit 1
fi
kube-linter lint k8s/ charts/redfish-controller/ && echo "kubernetes.policy: OK"
