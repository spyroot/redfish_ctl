#!/usr/bin/env bash
# telemetry.full-coverage (scheduled): verify every cataloged metric through
# the read-only Splunk MTS API. Protected CI injects the required environment;
# this script never reads, prints, or writes credential material.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python -m tools.splunk_full_coverage_gate "$@"
