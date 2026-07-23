#!/usr/bin/env bash
# repo.telemetry-docs-current (merge, mutates:false): the generated GB300
# telemetry metrics reference must match the exporter mapper and fixture corpus.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/generate_telemetry_metrics_doc.py --check
