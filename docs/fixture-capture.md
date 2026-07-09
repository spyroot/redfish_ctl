# Fixture Capture SOP

This SOP describes how I add Dell iDRAC Redfish fixtures for offline tests.
The goal is a small, redacted mockup that lets `tests/conftest.py`, the pytest
mock service, serve real-shaped Redfish resources without contacting hardware.

Default rule: do not capture from production hardware. Use an approved lab iDRAC,
capture only the read-only resources needed for the test, redact before import,
and prove the fixture with offline tests before committing it.

There are two capture paths. Use the one that fits:

- **A curated fixture set** (this SOP, below) — a handful of hand-selected, redacted resources for a
  specific test. Best for a single new command on an already-covered vendor.
- **A full vendor corpus** (next section) — the whole Redfish tree, crawled with `redfish_ctl`
  itself, committed under `tests/<vendor>_corpus/`. Best for adding a **new vendor or model**: one
  full capture lets many later commands be exercised offline with only a thin behavioral test.

## Contributing a Full Vendor Corpus (crawl the whole tree)

`redfish_ctl` can capture its own test data. The `discovery` command does a deep crawl of a BMC and
writes one JSON file per Redfish URI (plus `rest_api_map.npy`) under `~/.json_responses/<ip>/` —
exactly the layout the committed corpora use (`tests/supermicro_gb300_corpus/`,
`tests/supermicro_x10_fixtures/`). To contribute coverage for a BMC this project has not seen:

1. **Crawl an approved lab BMC** (read-only; never production, never someone else's hardware):

   ```bash
   export REDFISH_IP=<approved-lab-bmc>
   export REDFISH_USERNAME=<short-lived-readonly-account>
   export REDFISH_PASSWORD=<password>          # keep out of shell history
   redfish_ctl discovery                        # writes ~/.json_responses/<ip>/
   ```

2. **Sanitize the whole tree before it goes near the repo.** A full crawl contains identifiers, so
   this is mandatory for a public contribution — apply the same rules as [Redact Before
   Import](#redact-before-import) across every file, and **rename the `<ip>` directory to a
   documentation address** (e.g. `203.0.113.10`, `198.51.100.x`) so no real management IP is
   committed. Run the secret scan in that section over the entire captured directory, then read the
   files yourself.

3. **Place the sanitized tree** under a new corpus directory matching the existing convention:

   ```text
   tests/<vendor>_<model>_corpus/json_responses/<placeholder-ip>/_redfish_v1_....json
   ```

4. **Add a thin corpus-backed test** (see `tests/test_supermicro_gb300_corpus.py` and
   `tests/test_supermicro_x10_fixtures.py`) that loads the corpus and asserts a command's contract
   against it — URL/payload for mutations, `CommandResult` shape for reads — with no live BMC.

5. **Binaries go to Git LFS automatically** — `.gitattributes` already routes `*.npy`, `*.zip`,
   `*.exe`, and firmware trees to LFS; the Redfish JSON stays as plain committed text.

6. **Gate before committing** — the offline suite must stay green with the live variables cleared:

   ```bash
   env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD pytest -q
   ```

The rest of this document is the curated single-fixture path.

## Required Tools

- `Redfish-Mockup-Creator`, the DMTF tool from
  [DMTF/Redfish-Mockup-Creator](https://github.com/DMTF/Redfish-Mockup-Creator),
  creates a file tree from a live Redfish service.
- `tools/redfish_validate.py`, the repository schema helper, validates standard
  Redfish surfaces against cached DMTF schemas.
- `tests/idrac_fixtures/`, the Dell overlay directory loaded by
  `tests/conftest.py`, holds hand-curated fixture JSON for missing Dell paths.
- `redfish_ctl/json_responses/`, the captured DMTF mockup base tree used by the
  default mock service, stays generic and should not receive Dell-only overlays.

No API key is required. A capture needs only an approved lab BMC address and a
temporary BMC account with read-only permission for the resources being captured.
Never paste credentials into chat, docs, commit messages, logs, or test output.

## Capture

Run the capture from a trusted local shell. `REDFISH_IP`, the endpoint variable read
by this project and its tests, should point at an approved lab iDRAC. Use a
short-lived lab credential. The DMTF creator requires the password as a command
argument, so avoid shared hosts and do not preserve the command in shell history.

```bash
git clone https://github.com/DMTF/Redfish-Mockup-Creator.git \
  /tmp/Redfish-Mockup-Creator

cd /tmp/Redfish-Mockup-Creator
python3 -m venv /tmp/redfish-mockup-creator-venv
. /tmp/redfish-mockup-creator-venv/bin/activate
python -m pip install -r requirements.txt

printf 'iDRAC IP or host: '
read -r REDFISH_IP
printf 'iDRAC username: '
read -r REDFISH_USERNAME
read -rsp "iDRAC password: " REDFISH_PASSWORD
printf '\n'

python redfishMockupCreate.py \
  --user "$REDFISH_USERNAME" \
  --password "$REDFISH_PASSWORD" \
  --rhost "$REDFISH_IP" \
  --Secure \
  --Auth Session \
  --Dir /tmp/idrac-redfish-mockup
```

`--Dir`, the Mockup Creator output option, writes one directory per Redfish URI.
The creator stores JSON resources as `index.json` files under that tree. Keep the
raw output outside the repository until redaction and validation are complete.
Do not pass `--Headers` unless the current task explicitly needs headers and
you have a redaction plan for auth tokens, cookies, and request IDs.

## Select The Smallest Fixture Set

Import only the paths needed by the test package. For an S1 test, that usually
means one collection and one or two leaves, not a full BMC crawl.

Example mapping:

```text
/redfish/v1/Systems/System.Embedded.1/Bios
  -> tests/idrac_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios.json

/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService
  -> tests/idrac_fixtures/_redfish_v1_Managers_iDRAC.Embedded.1_Oem_Dell_DellJobService.json
```

The fixture filename is the Redfish path with slashes replaced by underscores
and a `.json` suffix. Do not include query strings in filenames. If a test needs
an expanded resource, store the expanded shape only when the command actually
depends on the expanded fields.

## Redact Before Import

Review every selected JSON file before it enters `tests/idrac_fixtures/`.
Remove or replace:

- BMC passwords, session tokens, cookies, auth headers, and API keys.
- Service tags, serial numbers, asset tags, UUIDs, WWNs, and license keys.
- MAC addresses, private IP addresses, DNS names, rack names, and user names.
- Certificate private material and raw auth responses.
- Any `Headers` capture that includes `X-Auth-Token`, `Set-Cookie`, or similar
  secret-class values.

Prefer stable placeholders that keep shape but do not identify hardware:

```json
{
  "SerialNumber": "REDACTED",
  "AssetTag": "REDACTED",
  "IPv4Addresses": []
}
```

Run a quick secret scan over the candidate files:

```bash
secret_pattern='password|passwd|token|secret|set-cookie|x-auth|session'
secret_pattern="${secret_pattern}|serial|servicetag|asset|license|wwn"
secret_pattern="${secret_pattern}|[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}"

rg -n "$secret_pattern" /tmp/fixture-candidates
```

This scan is only a guardrail. Read the files yourself before committing them.

## Validate

First prove the JSON parses:

```bash
python -m json.tool \
  tests/idrac_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios.json \
  >/dev/null
```

Then validate standard Redfish resources with `tools/redfish_validate.py`.
`REDFISH_SCHEMA_OFFLINE`, the environment variable read by that helper, prevents
network fetches and requires schemas already cached under `tools/redfish-schemas/`.

```bash
REDFISH_SCHEMA_OFFLINE=1 python - <<'PY'
import json
from pathlib import Path

from tools.redfish_validate import SchemaUnavailable, validate_payload

for path in sorted(Path("tests/idrac_fixtures").glob("*.json")):
    payload = json.loads(path.read_text())
    try:
        errors = validate_payload(payload)
    except (SchemaUnavailable, ValueError) as err:
        print(f"SKIP {path}: {err}")
        continue
    if errors:
        print(f"FAIL {path}")
        for err in errors:
            print(f"  {'/'.join(map(str, err.absolute_path))}: {err.message}")
        raise SystemExit(1)
    print(f"OK {path}")
PY
```

OEM resources often skip validation because no standard DMTF schema exists for
their private type. That is acceptable when the test asserts the exact command
behavior that depends on the OEM fields.

## Import

Copy only the redacted candidate files into `tests/idrac_fixtures/`. Do not copy
the whole Mockup Creator tree into the Dell overlay. If the full raw capture is
needed for later analysis, keep it outside the repository or in an approved
private artifact store.

After import, add or update a focused dual-mode test under `tests/test_*_dualmode.py`.
The test should assert the command contract:

- URL path or action target used by the command.
- POST or PATCH payload for mutating commands.
- `CommandResult` shape and key fields for read-only commands.
- No live iDRAC dependency when `REDFISH_IP` is unset.

## Verification Gate

Before committing, run the exact offline gate from a shell with the live variables
cleared:

```bash
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD \
  pytest -q

ruff check \
  tests/test_<package>_dualmode.py

python -m json.tool \
  tests/idrac_fixtures/<changed-fixture>.json \
  >/dev/null
```

For docs-only changes to this SOP, run:

```bash
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD \
  pytest -q

ruff check docs/fixture-capture.md
```

If the offline suite needs a live BMC, the fixture is not ready. Stop and record:

```text
BLOCKER: fixture still depends on live iDRAC behavior
Observation: <missing path, missing field, or failing test>
Safe next step: capture and redact the smallest missing Redfish resource
```
