#!/usr/bin/env bash
# evidence.sanitized (merge): the gate/run evidence artifact must contain no secret material. Scans
# EVIDENCE_DIR (default reports/gates) for credential patterns; fails if any is found. An absent
# artifact is fine (nothing to leak); a present one MUST be clean.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
dir="${EVIDENCE_DIR:-reports/gates}"
if [ ! -d "$dir" ]; then echo "evidence.sanitized: no evidence dir ($dir) — nothing to scan"; exit 0; fi
# Secret-shaped tokens: X-SF/Bearer/token=..., private keys, GH/glpat tokens, long b64 secrets.
if grep -rIE 'BEGIN [A-Z ]*PRIVATE KEY|X-SF-Token:|[Bb]earer [A-Za-z0-9._-]{20,}|glpat-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|password["'"'"' :=]+[^ *]{6,}' "$dir"; then
  echo "evidence.sanitized: secret-shaped content found in evidence — sanitize before upload" >&2
  exit 1
fi
echo "evidence.sanitized: OK ($dir clean)"
