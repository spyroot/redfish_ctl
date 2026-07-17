#!/usr/bin/env bash
# integration.namespace (integration, mutates:false): home-cluster reachability smoke. Fails CLOSED
# (BLOCKER) if the cluster is unreachable — never a silent pass, never a local fallback.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec ./scripts/k8s_ci.sh check
