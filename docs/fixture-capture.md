# Contributing Redfish Test Data (Fixtures and Corpora)

This guide explains how to contribute the offline test data `redfish_ctl` runs against: captured
Redfish resources that let the pytest mock service in `tests/conftest.py` serve real-shaped responses
for any vendor — Dell iDRAC, Supermicro, HPE iLO, or generic DMTF Redfish — with no hardware in the
loop. It is written for anyone adding coverage, not just the maintainers.
For the canonical corpus artifact layout and pull workflow, see [docs/corpus-library.md](corpus-library.md).

Default rule: never capture from production, or from hardware you are not authorized to read. Use an
approved lab BMC and a short-lived read-only account, sanitize every file before it enters the repo,
and prove the data with the offline suite before committing.

Pick the path that matches what you are contributing — the two are deliberately separate:

- **Path A — a BMC crawl.** Capture the selected Redfish tree with `redfish_ctl` itself and
  package it as either a dataset artifact or a filtered mock artifact. Best when adding a
  **new vendor or model**; one capture then backs many later commands with only a thin behavioral
  test. This is how the Supermicro GB300 and X10 mock corpora were contributed.
- **Path B — a curated fixture set.** Hand-pick, sanitize, and import only the *handful* of resources
  one specific test needs. Best when adding or fixing **one command on an already-covered vendor**.

## Path A — BMC Crawl (captured tree → dataset or mock artifact)

`redfish_ctl` can capture its own test data. The `discovery` command does a deep crawl of a BMC and
writes one JSON file per Redfish URI (plus `rest_api_map.npy`) under `~/.json_responses/<ip>/`.
Corpus archives are indexed in `corpora/manifest.v1.json`; see
[docs/corpus-library.md](corpus-library.md) before packaging a new capture. To contribute coverage for a
BMC this project has not seen:

1. **Crawl an approved lab BMC** (read-only; never production, never someone else's hardware):

   ```bash
   export REDFISH_IP=<approved-lab-bmc>
   export REDFISH_USERNAME=<short-lived-readonly-account>
   export REDFISH_PASSWORD=<password>          # keep out of shell history
   redfish_ctl discovery                        # writes ~/.json_responses/<ip>/
   ```

2. **Sanitize the tree — this is mandatory** (a crawl carries identifiers). Run the bundled
   redactor, which replaces the source management IP with an RFC 5737 documentation address, renames
   the `<ip>` directory, and scrubs identifier fields (serial/service tag, asset tag, UUID, MAC,
   hostname). It writes a clean copy into a staging directory for review before
   packaging it under `corpora/dataset/` or filtering it into a mock artifact under `corpora/mock/`:

   ```bash
   python tools/redact_corpus.py ~/.json_responses/<ip> \
     --out build/corpus-staging/<vendor>_<model>/json_responses
   ```

   It reports only counts (never values). When the source contains `rest_api_map.npy`, the redactor
   preserves `allowed_methods_mapping`, rewrites `url_file_mapping` to sanitized relative paths,
   writes `rest_api_map.v1.json`, regenerates the legacy `rest_api_map.npy`, and validates every
   mapped file exists. Treat it as a **first pass, not a guarantee**: then apply the [Redact Before
   Import](#redact-before-import) rules by hand for anything vendor-specific, run that section's
   secret scan over the output, and read the files yourself before committing.

3. **Add a thin corpus-backed test** (see `tests/test_supermicro_gb300_corpus.py` and
   `tests/test_supermicro_x10_fixtures.py`) that loads the corpus and asserts a command's contract
   against it — URL/payload for mutations, `CommandResult` shape for reads — with no live BMC.

4. **Binaries go to Git LFS automatically** — `.gitattributes` already routes `*.npy`, `*.zip`,
   `*.exe`, and firmware trees to LFS; the Redfish JSON stays as plain committed text.

5. **Gate before committing** — the offline suite must stay green with the live variables cleared:

   ```bash
   env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD pytest -q
   ```

## Path B — Curated fixture set (a few resources → one test)

Use this path to add or fix a single command on a vendor the suite already covers. You hand-pick,
sanitize, and import only the resources that test needs. The commands and file examples below use a
Dell iDRAC for concreteness, but the same steps apply to any vendor — capture from the approved lab
BMC, redact, and import under that vendor's fixture directory (`tests/dell_fixtures/`,
`tests/supermicro_fixtures/`, `tests/hpe_fixtures/`, `tests/generic_fixtures/`, …).

### Required Tools

- `Redfish-Mockup-Creator`, the DMTF tool from
  [DMTF/Redfish-Mockup-Creator](https://github.com/DMTF/Redfish-Mockup-Creator),
  creates a file tree from a live Redfish service.
- `tools/redfish_validate.py`, the repository schema helper, validates standard
  Redfish surfaces against cached DMTF schemas.
- `tests/dell_fixtures/`, the Dell overlay directory loaded by
  `tests/conftest.py`, holds hand-curated fixture JSON for missing Dell paths.
- `redfish_ctl/json_responses/`, the captured DMTF mockup base tree used by the
  default mock service, stays generic and should not receive Dell-only overlays.

No API key is required. A capture needs only an approved lab BMC address and a
temporary BMC account with read-only permission for the resources being captured.
Never paste credentials into chat, docs, commit messages, logs, or test output.

### Capture

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

### Select The Smallest Fixture Set

Import only the paths needed by the test package. For an S1 test, that usually
means one collection and one or two leaves, not a full BMC crawl.

Example mapping:

```text
/redfish/v1/Systems/System.Embedded.1/Bios
  -> tests/dell_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios.json

/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService
  -> tests/dell_fixtures/_redfish_v1_Managers_iDRAC.Embedded.1_Oem_Dell_DellJobService.json
```

The fixture filename is the Redfish path with slashes replaced by underscores
and a `.json` suffix. Do not include query strings in filenames. If a test needs
an expanded resource, store the expanded shape only when the command actually
depends on the expanded fields.

### Redact Before Import

Review every selected JSON file before it enters `tests/dell_fixtures/`.
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

### Validate

First prove the JSON parses:

```bash
python -m json.tool \
  tests/dell_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios.json \
  >/dev/null
```

Then validate standard Redfish resources with `tools/redfish_validate.py`.
`REDFISH_SCHEMA_OFFLINE`, the environment variable read by that helper, prevents
network fetches and requires schemas already cached under `tools/redfish-schemas/`.

```bash
REDFISH_SCHEMA_OFFLINE=1 \
  python tools/redfish_validate.py tests/dell_fixtures
```

The validator classifies each JSON file as valid, error, or skipped. Use
`--json` when a script needs the same result as machine-readable output. OEM
resources often skip validation because no standard DMTF schema exists for their
private type. That is acceptable when the test asserts the exact command behavior
that depends on the OEM fields.

### Import

Copy only the redacted candidate files into `tests/dell_fixtures/`. Do not copy
the whole Mockup Creator tree into the Dell overlay. If the full raw capture is
needed for later analysis, keep it outside the repository or in an approved
private artifact store.

After import, add or update a focused dual-mode test under `tests/test_*_dualmode.py`.
The test should assert the command contract:

- URL path or action target used by the command.
- POST or PATCH payload for mutating commands.
- `CommandResult` shape and key fields for read-only commands.
- No live iDRAC dependency when `REDFISH_IP` is unset.

### Verification Gate

Before committing, run the exact offline gate from a shell with the live variables
cleared:

```bash
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD \
  pytest -q

ruff check \
  tests/test_<package>_dualmode.py

python -m json.tool \
  tests/dell_fixtures/<changed-fixture>.json \
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
