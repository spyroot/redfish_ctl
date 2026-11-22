#!/usr/bin/env bash
# evidence.sanitized (merge): the gate/run evidence artifact must contain no secret material. Scans
# EVIDENCE_DIR (default reports) for credential patterns; fails if it is missing or contains a secret.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 -m tools.ci_evidence "${EVIDENCE_DIR:-reports}"
