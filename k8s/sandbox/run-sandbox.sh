#!/usr/bin/env bash
# Build and run the local read-path Kubernetes sandbox.
set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-redfish-sandbox}"
KIND_CONFIG="${KIND_CONFIG:-k8s/sandbox/kind-config.yaml}"
NAMESPACE="${NAMESPACE:-redfish-sandbox}"
SANDBOX_BACKENDS="${SANDBOX_BACKENDS:-corpus-mock}"
STATUS_TIMEOUT_SECONDS="${STATUS_TIMEOUT_SECONDS:-180}"
KUBECTL_CONTEXT="kind-${KIND_CLUSTER_NAME}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$REPO_ROOT"

IFS=',' read -r -a BACKENDS <<<"$SANDBOX_BACKENDS"

require_tool() {
	if ! command -v "$1" >/dev/null 2>&1; then
		printf 'missing required tool: %s\n' "$1" >&2
		exit 127
	fi
}

has_backend() {
	local selected

	for selected in "${BACKENDS[@]}"; do
		case "$selected" in
		"$1" | all)
			return 0
			;;
		esac
	done
	return 1
}

validate_backends() {
	local selected

	for selected in "${BACKENDS[@]}"; do
		case "$selected" in
		corpus-mock | ilo-sim | all)
			;;
		"")
			printf 'SANDBOX_BACKENDS contains an empty backend\n' >&2
			exit 2
			;;
		*)
			printf 'unknown SANDBOX_BACKENDS entry: %s\n' "$selected" >&2
			printf 'valid entries: corpus-mock, ilo-sim, all\n' >&2
			exit 2
			;;
		esac
	done
}

section() {
	printf '\n>> %s\n' "$1"
}

kubectl_sandbox() {
	kubectl --context "${KUBECTL_CONTEXT}" "$@"
}

wait_for_endpoint() {
	local endpoint_name="$1"
	local deadline
	local power_state

	deadline=$((SECONDS + STATUS_TIMEOUT_SECONDS))
	power_state=""
	while [ "$SECONDS" -lt "$deadline" ]; do
		power_state="$(
			kubectl_sandbox -n "${NAMESPACE}" \
				get redfishendpoint "$endpoint_name" \
				-o 'jsonpath={.status.powerState}' 2>/dev/null || true
		)"
		if [ -n "$power_state" ]; then
			case "$power_state" in
			On | Off | PoweringOn | PoweringOff | Paused)
				printf 'RedfishEndpoint %s powerState=%s\n' \
					"$endpoint_name" "$power_state"
				return 0
				;;
			*)
				# A populated but non-Redfish value means the controller
				# wrote garbage; fail instead of passing on any non-empty string.
				printf 'RedfishEndpoint %s reported invalid powerState=%s\n' \
					"$endpoint_name" "$power_state" >&2
				return 1
				;;
			esac
		fi
		sleep 5
	done

	printf 'timed out waiting for RedfishEndpoint %s status.powerState\n' \
		"$endpoint_name" >&2
	kubectl_sandbox -n "${NAMESPACE}" get redfishendpoint "$endpoint_name" \
		-o yaml || true
	return 1
}

assert_corpus_status() {
	# The corpus-mock backend is deterministic: the committed GB300 system_0
	# reports health OK and a non-empty thermal set. Assert the controller
	# surfaced those, not just that some status was written.
	local endpoint_name="$1"
	local health temp_count

	health="$(
		kubectl_sandbox -n "${NAMESPACE}" \
			get redfishendpoint "$endpoint_name" \
			-o 'jsonpath={.status.health}' 2>/dev/null || true
	)"
	temp_count="$(
		kubectl_sandbox -n "${NAMESPACE}" \
			get redfishendpoint "$endpoint_name" \
			-o 'jsonpath={.status.temperature.count}' 2>/dev/null || true
	)"

	if [ "$health" != "OK" ]; then
		printf 'RedfishEndpoint %s expected health=OK, got %s\n' \
			"$endpoint_name" "$health" >&2
		return 1
	fi
	if ! [ "$temp_count" -ge 1 ] 2>/dev/null; then
		printf 'RedfishEndpoint %s expected temperature.count>=1, got %s\n' \
			"$endpoint_name" "$temp_count" >&2
		return 1
	fi
	printf 'RedfishEndpoint %s status verified: health=%s temperature.count=%s\n' \
		"$endpoint_name" "$health" "$temp_count"
}

validate_backends
require_tool docker
require_tool kind
require_tool kubectl

section "building sandbox images"
if has_backend "corpus-mock"; then
	docker build \
		-f docker/Dockerfile.mock-bmc \
		-t redfish-ctl-mock-bmc:local \
		.
fi
if has_backend "ilo-sim"; then
	docker build \
		-f docker/Dockerfile.ilo-sim \
		-t redfish-ctl-ilo-sim:local \
		.
fi
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
if has_backend "corpus-mock"; then
	kind load docker-image redfish-ctl-mock-bmc:local --name "${KIND_CLUSTER_NAME}"
fi
if has_backend "ilo-sim"; then
	kind load docker-image redfish-ctl-ilo-sim:local --name "${KIND_CLUSTER_NAME}"
fi
kind load docker-image redfish-ctl-controller:local --name "${KIND_CLUSTER_NAME}"

section "applying sandbox resources"
kubectl_sandbox apply -f k8s/sandbox/namespace.yaml
kubectl_sandbox apply -f k8s/controller/redfish-endpoint-crd.yaml
if has_backend "corpus-mock"; then
	kubectl_sandbox apply -f k8s/sandbox/mock-bmc.yaml
	kubectl_sandbox apply -f k8s/sandbox/mock-credentials.yaml
fi
if has_backend "ilo-sim"; then
	kubectl_sandbox apply -f k8s/sandbox/ilo-sim.yaml
	kubectl_sandbox apply -f k8s/sandbox/ilo-credentials.yaml
fi
kubectl_sandbox apply -f k8s/controller/rbac.yaml
kubectl_sandbox apply -f k8s/controller/deployment.yaml
if has_backend "corpus-mock"; then
	kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-sample.yaml
fi
if has_backend "ilo-sim"; then
	kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-ilo-sim.yaml
fi

section "waiting for deployments"
if has_backend "corpus-mock"; then
	kubectl_sandbox -n "${NAMESPACE}" \
		rollout status deploy/mock-bmc \
		--timeout=120s
fi
if has_backend "ilo-sim"; then
	kubectl_sandbox -n "${NAMESPACE}" \
		rollout status deploy/ilo-sim \
		--timeout=120s
fi
kubectl_sandbox -n "${NAMESPACE}" \
	rollout status deploy/redfish-endpoint-controller \
	--timeout=120s

section "waiting for RedfishEndpoint status"
if has_backend "corpus-mock"; then
	wait_for_endpoint gb300-mock
	assert_corpus_status gb300-mock
fi
if has_backend "ilo-sim"; then
	wait_for_endpoint ilo-sim
fi
