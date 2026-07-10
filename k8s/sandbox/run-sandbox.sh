#!/usr/bin/env bash
# Build and run the local read-path Kubernetes sandbox.
set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-redfish-sandbox}"
KIND_CONFIG="${KIND_CONFIG:-k8s/sandbox/kind-config.yaml}"
NAMESPACE="${NAMESPACE:-redfish-sandbox}"
STATUS_TIMEOUT_SECONDS="${STATUS_TIMEOUT_SECONDS:-180}"
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

require_tool docker
require_tool kind
require_tool kubectl

section "building sandbox images"
docker build \
	-f docker/Dockerfile.mock-bmc \
	-t redfish-ctl-mock-bmc:local \
	.
docker build \
	-f docker/Dockerfile.controller \
	-t redfish-ctl-controller:local \
	.

if kind get clusters | grep -Fxq "${KIND_CLUSTER_NAME}"; then
	section "using existing kind cluster ${KIND_CLUSTER_NAME}"
else
	section "creating kind cluster ${KIND_CLUSTER_NAME}"
	kind create cluster --name "${KIND_CLUSTER_NAME}" --config "${KIND_CONFIG}"
fi

section "loading images into kind"
kind load docker-image redfish-ctl-mock-bmc:local --name "${KIND_CLUSTER_NAME}"
kind load docker-image redfish-ctl-controller:local --name "${KIND_CLUSTER_NAME}"

section "applying sandbox resources"
kubectl apply -f k8s/sandbox/namespace.yaml
kubectl apply -f k8s/controller/redfish-endpoint-crd.yaml
kubectl apply -f k8s/sandbox/mock-bmc.yaml
kubectl apply -f k8s/sandbox/mock-credentials.yaml
kubectl apply -f k8s/controller/rbac.yaml
kubectl apply -f k8s/controller/deployment.yaml
kubectl apply -f k8s/sandbox/redfish-endpoint-sample.yaml

section "waiting for deployments"
kubectl -n "${NAMESPACE}" rollout status deploy/mock-bmc --timeout=120s
kubectl -n "${NAMESPACE}" \
	rollout status deploy/redfish-endpoint-controller \
	--timeout=120s

section "waiting for RedfishEndpoint status"
deadline=$((SECONDS + STATUS_TIMEOUT_SECONDS))
power_state=""
while [ "$SECONDS" -lt "$deadline" ]; do
	power_state="$(
		kubectl -n "${NAMESPACE}" \
			get redfishendpoint gb300-mock \
			-o 'jsonpath={.status.powerState}' 2>/dev/null || true
	)"
	if [ -n "$power_state" ]; then
		printf 'RedfishEndpoint gb300-mock powerState=%s\n' "$power_state"
		exit 0
	fi
	sleep 5
done

printf 'timed out waiting for RedfishEndpoint status.powerState\n' >&2
kubectl -n "${NAMESPACE}" get redfishendpoint gb300-mock -o yaml || true
exit 1
