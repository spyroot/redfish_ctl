#!/usr/bin/env bash
# unit.all (merge): the full OFFLINE unit suite. Excludes the dmtf_sim_live lane
# — an integration lane that needs the deployed DMTF sim plus REDFISH_IP/
# REDFISH_PORT and FAILS CLOSED when they are absent. That lane runs in private
# CI with `pytest -m dmtf_sim_live`; here it is deselected so GitHub CI and this
# offline gate stay green without the sim.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec pytest -q -m "not dmtf_sim_live"
