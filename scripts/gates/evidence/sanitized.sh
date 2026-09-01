#!/usr/bin/env bash
# evidence.sanitized (merge): the gate/run evidence artifact must contain no secret material. Scans
# EVIDENCE_DIR (default reports) for credential patterns; fails if it is missing or contains a secret.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
dir="${EVIDENCE_DIR:-reports}"
if [ ! -d "$dir" ]; then
  echo "evidence.sanitized: required evidence dir is missing ($dir)" >&2
  exit 1
fi
# Secret-shaped tokens: X-SF/Bearer/token=..., private keys, GH/glpat tokens, long b64 secrets.
credential_pattern='BEGIN [A-Z ]*PRIVATE KEY|X-SF-Token:'
credential_pattern+='|[Bb]earer [A-Za-z0-9._-]{20,}|glpat-[A-Za-z0-9_-]{20,}'
credential_pattern+='|ghp_[A-Za-z0-9]{30,}|password["'"'"' :=]+[^ *]{6,}'
if grep -rIqE "$credential_pattern" "$dir"; then
  echo "evidence.sanitized: secret-shaped content found in evidence — sanitize before upload" >&2
  exit 1
else
  scan_status=$?
  if [ "$scan_status" -ne 1 ]; then
    echo "evidence.sanitized: evidence scan failed" >&2
    exit 1
  fi
fi
echo "evidence.sanitized: OK ($dir clean)"
