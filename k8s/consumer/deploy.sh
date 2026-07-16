#!/usr/bin/env bash
# Build, load, and deploy the fleet-status consumer into the sandbox cluster.
#
# The consumer reads RedfishEndpoint .status (written by the controller) and
# serves a dashboard, a JSON API, and Prometheus metrics. It never talks to a
# BMC and has no Secret access.
#
# Usage:
#   ./k8s/consumer/deploy.sh                 # build + kind load + apply + wait
#   PORT_FORWARD=1 ./k8s/consumer/deploy.sh  # then port-forward to localhost:8199
set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-redfish-sandbox}"

# The sandbox tears itself down after a green run; deploying into a missing
# cluster otherwise fails with an opaque kubectl context error.
if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER_NAME}"; then
	printf 'sandbox cluster "%s" is not running.\nStart it and keep it up first:  KEEP_CLUSTER=1 make k8s-sandbox\n' "${KIND_CLUSTER_NAME}" >&2
	exit 1
fi
NAMESPACE="${NAMESPACE:-redfish-sandbox}"
KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
IMAGE="${IMAGE:-redfish-ctl-consumer:local}"
LOCAL_PORT="${LOCAL_PORT:-8199}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$REPO_ROOT"

require_tool() {
	command -v "$1" >/dev/null 2>&1 || {
		printf 'missing required tool: %s\n' "$1" >&2
		exit 127
	}
}

section() { printf '\n>> %s\n' "$1"; }

kc() { kubectl --context "${KUBECTL_CONTEXT}" "$@"; }

require_tool docker
require_tool kind
require_tool kubectl

section "building ${IMAGE}"
docker build -f docker/Dockerfile.consumer -t "${IMAGE}" .

section "loading ${IMAGE} into kind cluster ${KIND_CLUSTER_NAME}"
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER_NAME}"

section "applying consumer RBAC, egress policy, and deployment"
kc apply -f k8s/consumer/rbac.yaml
# NetworkPolicy is a no-op under kind's kindnet CNI but is enforced on a
# NetworkPolicy-capable cluster; apply it so intent travels with the manifests.
kc apply -f k8s/consumer/networkpolicy.yaml
kc apply -f k8s/consumer/deployment.yaml

# On a reused cluster the :local tag is unchanged, so force a restart to pick up
# a freshly built image.
kc -n "${NAMESPACE}" rollout restart deploy/redfish-fleet-consumer >/dev/null 2>&1 || true

section "waiting for consumer rollout"
kc -n "${NAMESPACE}" rollout status deploy/redfish-fleet-consumer --timeout=120s

printf '\nconsumer ready. Reach it with:\n'
printf '  kubectl --context %s -n %s port-forward svc/redfish-fleet-consumer %s:80\n' \
	"${KUBECTL_CONTEXT}" "${NAMESPACE}" "${LOCAL_PORT}"
printf '  open http://127.0.0.1:%s/   (dashboard)  /api/nodes  /metrics\n' "${LOCAL_PORT}"

if [ "${PORT_FORWARD:-0}" = "1" ]; then
	section "port-forwarding svc/redfish-fleet-consumer -> localhost:${LOCAL_PORT}"
	exec kc -n "${NAMESPACE}" port-forward svc/redfish-fleet-consumer "${LOCAL_PORT}:80" --address 127.0.0.1
fi
