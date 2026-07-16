#!/usr/bin/env bash
# Build, load, and deploy the redfish_ctl web explorer into the sandbox cluster,
# pointed at one BMC. Every command selected in the UI is invoked live through
# the tool's own registry — no scripts, no ad-hoc HTTP.
#
# Usage:
#   REDFISH_IP=172.0.2.10 SECRET=bmc-credentials ./k8s/explorer/deploy.sh
#   PORT_FORWARD=1 ... ./k8s/explorer/deploy.sh    # then browse localhost:8299
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
IMAGE="${IMAGE:-redfish-ctl-explorer:local}"
LOCAL_PORT="${LOCAL_PORT:-8299}"
# Target BMC: address + Secret (username/password keys) in the namespace.
REDFISH_IP="${REDFISH_IP:-mock-bmc.redfish-sandbox.svc.cluster.local}"
REDFISH_PORT="${REDFISH_PORT:-80}"
REDFISH_SCHEME="${REDFISH_SCHEME:-http}"
SECRET="${SECRET:-mock-bmc-credentials}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$REPO_ROOT"

require_tool() { command -v "$1" >/dev/null 2>&1 || { printf 'missing tool: %s\n' "$1" >&2; exit 127; }; }
section() { printf '\n>> %s\n' "$1"; }
kc() { kubectl --context "${KUBECTL_CONTEXT}" "$@"; }

require_tool docker
require_tool kind
require_tool kubectl

section "building ${IMAGE}"
docker build -f docker/Dockerfile.explorer -t "${IMAGE}" .

section "loading ${IMAGE} into kind cluster ${KIND_CLUSTER_NAME}"
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER_NAME}"

section "applying explorer deployment (target ${REDFISH_SCHEME}://${REDFISH_IP}:${REDFISH_PORT}, secret ${SECRET})"
kc apply -f k8s/explorer/deployment.yaml
kc -n "${NAMESPACE}" set env deploy/redfish-ctl-explorer \
	REDFISH_IP="${REDFISH_IP}" REDFISH_PORT="${REDFISH_PORT}" REDFISH_SCHEME="${REDFISH_SCHEME}"
kc -n "${NAMESPACE}" patch deploy/redfish-ctl-explorer --type=json -p "[
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/env/3/valueFrom/secretKeyRef/name\",\"value\":\"${SECRET}\"},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/env/4/valueFrom/secretKeyRef/name\",\"value\":\"${SECRET}\"}
]"
kc -n "${NAMESPACE}" rollout restart deploy/redfish-ctl-explorer >/dev/null 2>&1 || true

section "waiting for explorer rollout"
kc -n "${NAMESPACE}" rollout status deploy/redfish-ctl-explorer --timeout=120s

printf '\nexplorer ready. Browse it with:\n'
printf '  kubectl --context %s -n %s port-forward svc/redfish-ctl-explorer %s:80\n' \
	"${KUBECTL_CONTEXT}" "${NAMESPACE}" "${LOCAL_PORT}"
printf '  open http://127.0.0.1:%s/\n' "${LOCAL_PORT}"

if [ "${PORT_FORWARD:-0}" = "1" ]; then
	section "port-forwarding svc/redfish-ctl-explorer -> localhost:${LOCAL_PORT}"
	exec kc -n "${NAMESPACE}" port-forward svc/redfish-ctl-explorer "${LOCAL_PORT}:80" --address 127.0.0.1
fi
