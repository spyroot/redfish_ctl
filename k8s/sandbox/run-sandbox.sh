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

node_profile_field() {
	kubectl_sandbox -n "${NAMESPACE}" \
		get redfishnodeprofile "$1" -o "jsonpath=$2" 2>/dev/null || true
}

wait_for_node_profile_plan() {
	# Wait until the controller has produced a dry-run plan hash for the profile.
	local name="$1"
	local deadline
	local plan_hash

	deadline=$((SECONDS + STATUS_TIMEOUT_SECONDS))
	while [ "$SECONDS" -lt "$deadline" ]; do
		plan_hash="$(node_profile_field "$name" '{.status.planHash}')"
		if [ -n "$plan_hash" ]; then
			printf '%s\n' "$plan_hash"
			return 0
		fi
		sleep 3
	done

	printf 'timed out waiting for RedfishNodeProfile %s plan\n' "$name" >&2
	kubectl_sandbox -n "${NAMESPACE}" get redfishnodeprofile "$name" -o yaml >&2 || true
	return 1
}

wait_for_node_profile_applied() {
	# Wait until the approved plan is applied and consumed (one-shot approval).
	local name="$1"
	local plan_hash="$2"
	local deadline
	local applied
	local consumed

	deadline=$((SECONDS + STATUS_TIMEOUT_SECONDS))
	while [ "$SECONDS" -lt "$deadline" ]; do
		applied="$(node_profile_field "$name" \
			'{.status.conditions[?(@.type=="Applied")].status}')"
		consumed="$(node_profile_field "$name" '{.status.consumedPlanHash}')"
		if [ "$applied" = "True" ] && [ "$consumed" = "$plan_hash" ]; then
			printf 'RedfishNodeProfile %s applied (consumedPlanHash=%s)\n' \
				"$name" "$consumed"
			return 0
		fi
		sleep 3
	done

	printf 'timed out waiting for RedfishNodeProfile %s apply\n' "$name" >&2
	kubectl_sandbox -n "${NAMESPACE}" get redfishnodeprofile "$name" -o yaml >&2 || true
	return 1
}

wait_for_node_profile_converged() {
	# After apply the mock reflects the change, so the next plan finds no drift.
	local name="$1"
	local deadline
	local drift

	deadline=$((SECONDS + STATUS_TIMEOUT_SECONDS))
	while [ "$SECONDS" -lt "$deadline" ]; do
		drift="$(node_profile_field "$name" \
			'{.status.conditions[?(@.type=="DriftDetected")].status}')"
		if [ "$drift" = "False" ]; then
			printf 'RedfishNodeProfile %s converged (no drift after apply)\n' "$name"
			return 0
		fi
		sleep 3
	done

	printf 'timed out waiting for RedfishNodeProfile %s to converge\n' "$name" >&2
	kubectl_sandbox -n "${NAMESPACE}" get redfishnodeprofile "$name" -o yaml >&2 || true
	return 1
}

drive_node_profile() {
	# Exercise the gated write path end to end: plan -> approve -> apply -> converge.
	local name="$1"
	local plan_hash

	section "waiting for RedfishNodeProfile ${name} plan"
	plan_hash="$(wait_for_node_profile_plan "$name")"
	printf 'RedfishNodeProfile %s planHash=%s (drift detected, approving)\n' \
		"$name" "$plan_hash"

	kubectl_sandbox -n "${NAMESPACE}" patch redfishnodeprofile "$name" \
		--type=merge -p "{\"spec\":{\"approvedPlanHash\":\"${plan_hash}\"}}"

	section "waiting for RedfishNodeProfile ${name} apply"
	wait_for_node_profile_applied "$name" "$plan_hash"

	section "waiting for RedfishNodeProfile ${name} convergence"
	wait_for_node_profile_converged "$name"
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

cluster_reused=0
if kind get clusters | grep -Fxq "${KIND_CLUSTER_NAME}"; then
	section "using existing kind cluster ${KIND_CLUSTER_NAME}"
	cluster_reused=1
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
kubectl_sandbox apply -f k8s/controller/redfish-node-profile-crd.yaml
if has_backend "corpus-mock"; then
	# Publish the mutation rules the mock Deployment mounts at /rules so the
	# write path has somewhere to apply changes.
	kubectl_sandbox -n "${NAMESPACE}" create configmap mock-bmc-mutation-rules \
		--from-file=supermicro_gb300.yaml=tests/mutation_rules/supermicro_gb300.yaml \
		--dry-run=client -o yaml | kubectl_sandbox apply -f -
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
	# Recreate the profile so each run starts from a clean approval state: a
	# status.consumedPlanHash left by a prior run would block re-approval and
	# the apply would never fire.
	kubectl_sandbox -n "${NAMESPACE}" delete redfishnodeprofile gb300-mock \
		--ignore-not-found
	kubectl_sandbox apply -f k8s/sandbox/redfish-node-profile-sample.yaml
fi
if has_backend "ilo-sim"; then
	kubectl_sandbox apply -f k8s/sandbox/redfish-endpoint-ilo-sim.yaml
fi

if [ "$cluster_reused" = "1" ]; then
	# On a reused cluster the :local image tag is unchanged, so `kubectl apply`
	# alone will not recreate pods to pick up a freshly built image. Force a
	# restart so a re-run always runs the newly loaded images.
	section "restarting workloads to pick up rebuilt images"
	if has_backend "corpus-mock"; then
		kubectl_sandbox -n "${NAMESPACE}" rollout restart deploy/mock-bmc
	fi
	if has_backend "ilo-sim"; then
		kubectl_sandbox -n "${NAMESPACE}" rollout restart deploy/ilo-sim
	fi
	kubectl_sandbox -n "${NAMESPACE}" rollout restart deploy/redfish-endpoint-controller
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

# Write/CONVERGE leg: drive the RedfishNodeProfile plan -> approve -> apply path.
if has_backend "corpus-mock"; then
	drive_node_profile gb300-mock
fi
