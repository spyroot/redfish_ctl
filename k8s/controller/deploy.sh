#!/usr/bin/env bash
# Apply the controller CRDs and raw Kubernetes manifests from this checkout.
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$REPO_ROOT"

require_tool() {
	if ! command -v "$1" >/dev/null 2>&1; then
		printf 'missing required tool: %s\n' "$1" >&2
		exit 127
	fi
}

section() {
	printf '\n>> %s\n' "$1"
}

require_tool "$KUBECTL"

section "applying Redfish controller CRDs"
"$KUBECTL" apply -f k8s/controller/redfish-endpoint-crd.yaml
"$KUBECTL" apply -f k8s/controller/redfish-node-profile-crd.yaml

section "applying Redfish controller RBAC"
"$KUBECTL" apply -f k8s/controller/rbac.yaml

section "applying Redfish controller deployment"
"$KUBECTL" apply -f k8s/controller/deployment.yaml

section "waiting for controller rollout"
"$KUBECTL" -n redfish-sandbox \
	rollout status deploy/redfish-endpoint-controller \
	--timeout=120s
