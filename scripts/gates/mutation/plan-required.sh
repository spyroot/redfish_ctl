#!/usr/bin/env bash
# mutation.plan-required (deploy): a live apply may not proceed without a produced plan. Requires
# PLAN_ARTIFACT to point at an existing plan file. Fails loudly (never passes) when absent.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if [ -z "${PLAN_ARTIFACT:-}" ] || [ ! -s "${PLAN_ARTIFACT}" ]; then
  echo "mutation.plan-required: no plan artifact (set PLAN_ARTIFACT to a non-empty plan file)" >&2
  exit 1
fi
echo "mutation.plan-required: OK (plan ${PLAN_ARTIFACT})"
