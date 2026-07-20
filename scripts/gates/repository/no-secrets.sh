#!/usr/bin/env bash
# repo.no-secrets (merge): scan the working tree for committed secrets. Requires gitleaks in the gate
# toolchain — a missing scanner FAILS the gate (a skipped secret scan is never an implicit pass).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "repo.no-secrets: gitleaks not installed in this gate environment" >&2
  exit 1
fi
gitleaks detect --no-banner --redact --source . \
  && echo "repo.no-secrets: OK"
