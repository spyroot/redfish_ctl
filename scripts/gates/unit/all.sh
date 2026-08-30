#!/usr/bin/env bash
# unit.all (merge): the full OFFLINE unit suite. Hardware, emulator, and
# dmtf_sim_live tests are explicit profile exclusions, not runtime skips. The
# DMTF lane runs separately in private CI and fails closed when its endpoint is
# absent. The vendored-schema-only module is also an explicit profile exclusion;
# repo.schemas owns the required exact-schema validation path.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

# The compatibility fixture can select hardware from inherited connection
# variables even when its caller has no live marker. Keep the required gate
# hermetic until that fixture is migrated to the canonical lane contract.
unset \
  REDFISH_IP REDFISH_USERNAME REDFISH_PASSWORD REDFISH_PORT \
  IDRAC_IP IDRAC_USERNAME IDRAC_PASSWORD IDRAC_PORT

exec pytest -q -ra -W error \
  -m "not live and not emulator_live and not dmtf_sim_live" \
  --ignore=tests/gates/test_redfish_schema.py
