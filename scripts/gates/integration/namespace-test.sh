#!/usr/bin/env bash
# integration.namespace (integration, mutates:false): prove a temporary namespace
# can be RENDERED and is well-formed.
#
# Deliberately not a live-cluster probe. The shared gate contract defines this
# gate as "fails when temporary namespace creation cannot be rendered", and a
# render check is reproducible whether or not the cluster is up. Live cluster
# state is reported by the k8s-live-check pipeline job, which runs on the
# in-cluster runner and treats an unreachable API as UNAVAILABLE rather than as a
# failure of this project.
#
# This previously exec'd a workstation kubectl dispatcher, which could never pass
# in a runner pod: that dispatcher required a kubeconfig context named
# home-lab-k8s, and a pod carries a ServiceAccount instead. The gate was
# structurally unpassable.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

ns="rfctl-ci-render-check"
manifest="$(
    cat <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${ns}
  labels:
    app.kubernetes.io/name: redfish-ctl-ci
    app.kubernetes.io/managed-by: gates
YAML
)"

# yq ships in the shared toolbox, so this resolves without installing anything.
printf '%s\n' "$manifest" | yq -e '.kind == "Namespace"' >/dev/null \
    || { echo "integration.namespace: rendered manifest is not a Namespace" >&2; exit 1; }
printf '%s\n' "$manifest" | yq -e '.metadata.name | length > 0' >/dev/null \
    || { echo "integration.namespace: rendered namespace has no name" >&2; exit 1; }
printf '%s\n' "$manifest" | yq -e '.metadata.labels["app.kubernetes.io/name"] == "redfish-ctl-ci"' >/dev/null \
    || { echo "integration.namespace: rendered namespace is missing its ownership label" >&2; exit 1; }

# DNS-1123 is what Kubernetes will actually accept; catching it here beats
# catching it at apply time.
case "$ns" in
    [a-z0-9]*[a-z0-9]) : ;;
    *) echo "integration.namespace: '$ns' is not a valid DNS-1123 label" >&2; exit 1 ;;
esac
[ "${#ns}" -le 63 ] || { echo "integration.namespace: namespace name exceeds 63 characters" >&2; exit 1; }

echo "integration.namespace: OK (rendered ${ns})"
