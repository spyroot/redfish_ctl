#!/usr/bin/env bash
# mutation.protected-apply (deploy, mutates:true): a live apply may run ONLY from a protected pipeline.
# Refuses unless the pipeline is protected (CI_COMMIT_REF_PROTECTED=true) or a merge-request pipeline is
# detected (then it always refuses). This is the last line stopping an apply from an MR pipeline.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if [ "${CI_PIPELINE_SOURCE:-}" = "merge_request_event" ]; then
  echo "mutation.protected-apply: REFUSED — live apply is forbidden in a merge-request pipeline" >&2
  exit 1
fi
if [ "${CI_COMMIT_REF_PROTECTED:-false}" != "true" ] && [ "${ALLOW_PROTECTED_APPLY:-}" != "1" ]; then
  echo "mutation.protected-apply: REFUSED — not a protected pipeline (needs CI_COMMIT_REF_PROTECTED=true)" >&2
  exit 1
fi
echo "mutation.protected-apply: OK (protected pipeline)"
