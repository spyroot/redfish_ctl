#!/usr/bin/env bash
# Vendor the DSP8010 Redfish JSON schemas needed by our fixtures into
# tools/redfish-schemas/ by validating each fixture once (online). Files are
# cached for offline reuse, so the schema gate then runs with no network.
#
# Run once locally or in CI before `pytest -m schema`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 - <<'PY'
import glob, json, pathlib
from tools.redfish_validate import validate_payload, SchemaUnavailable

paths = sorted(glob.glob("tests/idrac_fixtures/*.json")
               + glob.glob("redfish_ctl/json_responses/*.json"))
validated = skipped = 0
for p in paths:
    payload = json.loads(pathlib.Path(p).read_text())
    if "@odata.type" not in payload:
        continue
    try:
        validate_payload(payload)
        validated += 1
    except SchemaUnavailable:
        skipped += 1  # OEM type or no standard schema
    except Exception as exc:  # noqa: BLE001 - report, keep warming the rest
        print(f"warn {p}: {exc}")
print(f"warmed schemas; validated {validated}, skipped {skipped} (OEM/no-schema)")
PY

echo ">> schemas cached under tools/redfish-schemas/"
