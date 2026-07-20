#!/usr/bin/env bash
# meta.ci-runner-tags (merge): every GitLab CI job carries the homelab-k8s runner tag (checked by the
# meta-gate when .gitlab-ci.yml is present; skips cleanly before GitLab lands).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/gate_meta.py
