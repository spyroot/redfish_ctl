#!/usr/bin/env bash
# scm.exact-commit (repository-export, mutates:false): the checkout being exported must be the exact
# commit CI declared, not a floating ref.
#
# The public-boundary chain is only meaningful if every boundary gate inspected the SAME commit that
# gets pushed. A pipeline that resolved a branch name could gate commit A and export commit B, and
# every gate would still report green. Comparing HEAD to CI_COMMIT_SHA closes that window.
#
# Fails CLOSED when running in CI: if CI_COMMIT_SHA is set, HEAD must equal it. Outside CI the
# variable does not exist and there is nothing to compare, so the gate reports skipped-not-applicable
# rather than inventing a pass — but the export profile only ever runs in CI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

declared="${CI_COMMIT_SHA:-}"
if [ -z "$declared" ]; then
    echo "scm.exact-commit: CI_COMMIT_SHA is unset — not running under CI, nothing to compare" >&2
    echo "  This gate belongs to the repository-export profile, which runs only in a pipeline." >&2
    exit 1
fi

head="$(git rev-parse HEAD)"
if [ "$head" != "$declared" ]; then
    echo "scm.exact-commit: checkout is NOT the commit CI declared" >&2
    echo "  HEAD           = $head" >&2
    echo "  CI_COMMIT_SHA  = $declared" >&2
    echo "  Every boundary gate must inspect the commit that will be pushed. Refusing." >&2
    exit 1
fi

echo "scm.exact-commit: OK ($head)"
