#!/usr/bin/env bash
# integration.namespace (integration, mutates:false): resolve and validate the
# namespace selected by the Builder project-service binding.
#
# This gate never creates a Namespace. Builder owns namespace bootstrap and the
# bounded namespace executor. The consumer supplies only its runtime CI project
# identity and validates the namespace returned by the provider resolver.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
source scripts/gates/integration/project_service_binding.bash

consumer="${CI_PROJECT_NAME:-}"
if [ -z "$consumer" ]; then
    echo "BLOCKER: integration.namespace requires CI_PROJECT_NAME from GitLab" >&2
    echo "SAFE_NEXT_STEP: run this gate through the registered Builder project CI path" >&2
    exit 78
fi
case "$consumer" in
    *[!A-Za-z0-9._-]*)
        echo "integration.namespace: CI_PROJECT_NAME is invalid" >&2
        exit 1
        ;;
esac

set +e
binding="$(project_service_binding_resolve "$consumer")"
resolve_status=$?
set -e
if [ "$resolve_status" -ne 0 ]; then
    echo "integration.namespace: Builder binding resolution failed" >&2
    exit "$resolve_status"
fi
set +e
coordinates="$(project_service_binding_coordinates "$binding")"
coordinate_status=$?
set -e
if [ "$coordinate_status" -ne 0 ]; then
    echo "integration.namespace: Builder binding coordinates are incomplete" >&2
    exit "$coordinate_status"
fi
IFS=$'\t' read -r namespace service_host service_port transport <<<"$coordinates"
project_service_coordinates_valid \
    "$namespace" "$service_host" "$service_port" "$transport" \
    || { echo "integration.namespace: Builder binding coordinates are invalid" >&2; exit 1; }

echo "integration.namespace: OK (Builder-owned ${namespace}; service ${transport}://${service_host}:${service_port})"
