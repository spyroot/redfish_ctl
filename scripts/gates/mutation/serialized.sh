#!/usr/bin/env bash
# mutation.serialized (deploy): applies against a given target must not run concurrently. Requires a
# held mutation lock (MUTATION_LOCK — e.g. a GitLab resource_group token or a k8s Lease holder id).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if [ -z "${MUTATION_LOCK:-}" ]; then
  echo "mutation.serialized: no mutation lock held (set MUTATION_LOCK; GitLab resource_group / k8s Lease)" >&2
  exit 1
fi
echo "mutation.serialized: OK (lock ${MUTATION_LOCK})"
