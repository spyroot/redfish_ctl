#!/usr/bin/env bash
# mutation.same-commit (deploy): the apply must run against the exact commit the plan was produced from,
# so nothing changed between plan and apply. Requires PLAN_COMMIT; fails on mismatch.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
head_commit="$(git rev-parse HEAD)"
if [ -z "${PLAN_COMMIT:-}" ]; then
  echo "mutation.same-commit: PLAN_COMMIT not set (the plan's commit)" >&2; exit 1
fi
if [ "${PLAN_COMMIT}" != "${head_commit}" ]; then
  echo "mutation.same-commit: plan ${PLAN_COMMIT} != apply ${head_commit} — re-plan on this commit" >&2
  exit 1
fi
echo "mutation.same-commit: OK (${head_commit})"
