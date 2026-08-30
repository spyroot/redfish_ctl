#!/usr/bin/env bash
# Resolve and validate Builder-owned project-service coordinates.

# Summary: Resolve the project-service binding for one runtime consumer.
# Arguments: consumer project name. Stdout: Builder binding JSON.
# Exit classes: provider resolver status. Side effects: read-only inventory access.
# Idempotency: deterministic for one provider inventory revision. Cleanup: none.
project_service_binding_resolve() {
    local consumer="$1" resolver
    resolver="$(command -v builder-project-resolve-binding 2>/dev/null || true)"
    if [ -z "$resolver" ]; then
        echo "BLOCKER: Builder project-service resolver is unavailable" >&2
        echo "SAFE_NEXT_STEP: use the toolbox image declared by the Builder binding" >&2
        return 78
    fi
    "$resolver" --consumer "$consumer" --format json
}

# Summary: Extract the namespace and service endpoint from a resolved binding.
# Arguments: binding JSON. Stdout: tab-separated namespace, host, port, transport.
# Exit classes: jq status. Side effects: none. Idempotency: deterministic. Cleanup: none.
project_service_binding_coordinates() {
    local binding="$1"
    printf '%s\n' "$binding" | jq -er '
        [
          (.kubernetes.namespace | select(type == "string" and length > 0)),
          (.endpoint.host | select(type == "string" and length > 0)),
          (.endpoint.port | select(type == "number" and floor == .)),
          (.endpoint.transport | select(. == "http" or . == "https"))
        ] | @tsv
    '
}

# Summary: Validate one Kubernetes DNS-1123 label.
# Arguments: label. Stdout: none. Exit classes: 0 valid, 1 invalid.
# Side effects: none. Idempotency: deterministic. Cleanup: none.
project_service_dns_label_valid() {
    local label="$1"
    [ "${#label}" -le 63 ] || return 1
    [[ "$label" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]
}

# Summary: Validate one Kubernetes-style DNS hostname.
# Arguments: hostname. Stdout: none. Exit classes: 0 valid, 1 invalid.
# Side effects: none. Idempotency: deterministic. Cleanup: none.
project_service_dns_host_valid() {
    local host="$1" label
    local -a labels
    [ "${#host}" -le 253 ] || return 1
    IFS='.' read -r -a labels <<<"$host"
    [ "${#labels[@]}" -gt 0 ] || return 1
    for label in "${labels[@]}"; do
        project_service_dns_label_valid "$label" || return 1
    done
}

# Summary: Validate namespace and service endpoint coordinates.
# Arguments: namespace, host, port, transport. Stdout: none.
# Exit classes: 0 valid, 1 invalid. Side effects: none.
# Idempotency: deterministic. Cleanup: none.
project_service_coordinates_valid() {
    local namespace="$1" host="$2" port="$3" transport="$4"
    project_service_dns_label_valid "$namespace" || return 1
    project_service_dns_host_valid "$host" || return 1
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || return 1
    [ "$transport" = "http" ] || [ "$transport" = "https" ]
}
