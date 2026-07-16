# Full (training) corpus contract

Two kinds of corpus artifact exist in this project, and they are **separate**:

| kind | purpose | producer | location | `artifact_type` |
| --- | --- | --- | --- | --- |
| **compact mock** | fast offline tests + the mock BMC | `tools/pack_corpus.py` (filters) | `tests/*_corpus.tar.gz` (public, LFS) | `compact_mock` |
| **full training** | complete capture for training/analysis | `tools/pack_full_corpus.py` (no filter) | `full_corpus/*.tar.gz` (committed, LFS) | `full_training` |

A compact mock **must not** overwrite, replace, or be mistaken for a full training
corpus. The mock may drop schemas/registries/log entries; the full corpus **may not**.

## Binding rule — full corpora are complete
A full training corpus keeps **everything** `redfish_ctl discovery` produced — every
JSON resource, all `Actions`/`@Redfish.AllowableValues`/`@Redfish.Settings`, all
attribute/BIOS/network/OEM registries, all `JsonSchemas`, all OEM resources — plus the
**exact** `rest_api_map.npy` from the same run and a `corpus_manifest.json`. Do not
filter, do not regenerate the map from filenames, do not downgrade a writable method.

## Producer
```bash
redfish_ctl discovery                                  # -> ~/.json_responses/<host-id>/{*.json, rest_api_map.npy}
python tools/pack_full_corpus.py ~/.json_responses/<host-id> \
    full_corpus/<vendor>_<model>_full_corpus.tar.gz --vendor <v> --model <m>
```
The archive unpacks to exactly one `<host-id>/` directory containing all JSON, the
same-run `rest_api_map.npy`, and `corpus_manifest.json`.

## Redaction — credentials + usernames ONLY
`pack_full_corpus.py` scrubs only credential/secret values (Password, SHA256Password,
IPMIKey, MD5v3Key, community strings, tokens, private keys, …) and account **usernames** —
a leaked password hash / IPMI key / community string is a real secret even on lab gear.
**Everything else is left original** — serials, MACs, IPs, hostnames, schemas, registries.

The captured hosts are **ephemeral lab gear** (destroyed within weeks), so their IPs, MACs,
hostnames, and serials are **not secret**. The full corpus is therefore **committed
publicly** in Git-LFS under `full_corpus/` — a **separate** structure from the compact mock
in `tests/` — with only credentials and usernames redacted; everything else is published as
captured.

### A redacted secret key is NOT a leak
Seeing a secret-named key in the corpus with the value `"REDACTED"` — e.g. `SHA256Password`,
`IPMIKey`, `MD5v3Key`, `SHA1v3Key`, `ROCommunity` — means the redactor **did its job**. The
key structure stays (so the corpus keeps its shape) while the value is scrubbed. A leak is a
secret-named key whose value is a **real** hash/key/community string, not `"REDACTED"`. The
class is matched by suffix pattern (`*v3key`, `*community`) plus an explicit set, so a new
vendor spelling of a known secret class (the way Dell's `SHA1v3Key` once slipped past an
exact-match list) is still caught. Always **re-scan a packed corpus for surviving secret
values before publishing**; `tests/test_full_corpus_contract.py` pins the redaction rules.

## Lossless capture — original response + status, including errors
`redfish_ctl discovery` saves the **original response body and the original HTTP status
code for every URL it touches**, so nothing is silently normalized or dropped:
- a **2xx** response → its body is a `_redfish_v1_….json` file in `url_file_mapping`;
- a **non-2xx** response (404/405/403/400…) is real ground truth for a simulator, so its
  body is kept too, saved as `_redfish_v1_….error.json` and recorded in
  `error_file_mapping` (a URL-keyed replay consumer keys on the plain `.json` name, so an
  `.error.json` is never mistaken for a live 200);
- the exact status of **every** URL (2xx and error, plus `0` for an unreachable/transport
  failure that has no body) lives in `http_status_mapping`.

The ServiceRoot (`/redfish/v1/` → `_redfish_v1.json`) is always persisted — it is the one
resource every consumer needs.

## `rest_api_map.npy` contract
Loads with `np.load(path, allow_pickle=True).item()`. Two required top-level keys (the
stable IGC contract), plus two additive capture keys:
- `url_file_mapping`: `{ "/redfish/v1/…": "_redfish_v1_….json" }` — 2xx resources only;
  every value is a file present in the same host dir; every 2xx resource JSON has a URL entry.
- `allowed_methods_mapping`: `{ "/redfish/v1/…": ["GET","HEAD","PATCH", …] }` — the real
  discovered HTTP methods, preserved exactly; a writable endpoint is **never** collapsed
  to `GET`/`HEAD`. (An old BMC that sends no `Allow` header yields empty lists — faithful,
  not a defect.)
- `http_status_mapping` *(additive)*: `{ "/redfish/v1/…": 200 | 404 | 403 | 0 }` — the
  original status for every touched URL.
- `error_file_mapping` *(additive)*: `{ "/redfish/v1/…": "_redfish_v1_….error.json" }` —
  captured non-2xx bodies. A URL is in **either** `url_file_mapping` or `error_file_mapping`,
  never both. Consumers that read only the two legacy keys are unaffected.

## `rest_api_map.status.json` sidecar
`rest_api_map.status.json`, written by `Discovery.save_url_file_mapping` next to
`rest_api_map.npy`, contains the additive `http_status_mapping` and `error_file_mapping` keys in
plain JSON. The mock BMC reads this sidecar first, so status/error replay does not require NumPy or
pickle loading for new captures. If the sidecar exists but is malformed or missing either replay map,
mock startup fails closed instead of falling back silently. Older corpora without the sidecar keep
using the legacy `.npy` contract.

## Validation gate (fail closed)
`pack_full_corpus.py` refuses to write unless: all JSON parse; the map loads and has the two
required mappings; the ServiceRoot is present and mapped;
`json_file_count == len(url_file_mapping) + len(error_file_mapping)`; every mapped file
(2xx **and** error) exists; no resource JSON is unmapped; no URL is in both the 2xx and
error mappings; every methods-URL is in one of the mappings; and every `http_status_mapping`
URL (unless status `0`) is mapped. `tests/test_full_corpus_contract.py` pins all of this,
including the error-capture cases. The two additive keys are optional, so pre-capture corpora
still validate.

## Per-vendor structure — as shipped (verified)
The committed maps share the required skeleton — one IP-named top dir holding the flattened
`_redfish_v1_*.json` files, a `rest_api_map.npy`, optional `rest_api_map.status.json`, and a
`corpus_manifest.json` — but they do **not** have identical keys or value formats. A consumer must
normalize, not assume:

| corpus | npy keys | status sidecar | `url_file_mapping` values |
| --- | --- | --- | --- |
| `dell_xr8620t` | 2 (`url_file_mapping`, `allowed_methods_mapping`) | absent | bare filenames |
| `hpe_dl360` | 2 | absent | absolute capture-host paths |
| `supermicro_gb300` | 2 | absent | absolute capture-host paths |
| `supermicro_x10` | 4 (adds `http_status_mapping`, `error_file_mapping`) | present | absolute capture-host paths |

So a consumer **must** (1) resolve `url_file_mapping`/`error_file_mapping` values by their
basename — older captures store `/…/.json_responses/<ip>/…` absolute paths, not filenames — and
(2) **tolerate the 2-key form**: default the success status to `200` and expose no error
examples when `http_status_mapping`/`error_file_mapping` are absent (only captures taken after
the lossless-capture change carry them). All four resolve fully today (every mapped basename
exists in the archive).

`tests/test_full_corpus_contract.py::test_committed_full_corpus_npy_is_sound` is the hard gate
for this: it extracts each committed tarball and asserts the npy loads, carries the two required
keys plus only known keys, and every `url_file_mapping` value resolves to a real JSON file by
basename. When a map carries `http_status_mapping` and `error_file_mapping`, the test also requires
a matching `rest_api_map.status.json` sidecar. A corrupt or incomplete shipped map fails the build.

`corpus_manifest.json` records `schema_version`, `artifact_type`, vendor/model/host_id,
`redfish_version`, the counts (`json_file_count`, `url_file_mapping_count`,
`error_file_mapping_count`, `allowed_methods_mapping_count`), per-method `method_counts`,
`http_status_counts`, `redaction_status`, and `artifact_checksum`. The `artifact_checksum`
field is a deterministic `sha256:` digest over the
staged payload files, excluding `corpus_manifest.json` so the checksum can be stored inside the
manifest without a self-reference cycle.
