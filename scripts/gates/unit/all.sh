#!/usr/bin/env bash
# unit.all (merge): the full OFFLINE unit suite. Live BMC/emulator tests and the
# dmtf_sim_live lane are explicit profile exclusions, not runtime skips. The
# DMTF lane runs separately in private CI and fails closed when its endpoint is
# absent. The vendored-schema-only module is also an explicit profile exclusion;
# repo.schemas owns the required exact-schema validation path.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec pytest -q -ra -W error \
  -m "not live and not dmtf_sim_live" \
  --ignore=tests/gates/test_redfish_schema.py
