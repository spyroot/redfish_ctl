#!/usr/bin/env bash
# meta.required-jobs (merge): the required GitLab jobs exist and none uses allow_failure:true; no
# live-apply job is reachable from a merge-request pipeline. Enforced by the meta-gate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/gate_meta.py
