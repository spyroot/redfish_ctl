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

## `rest_api_map.npy` contract
Loads with `np.load(path, allow_pickle=True).item()` and has exactly two required
top-level keys:
- `url_file_mapping`: `{ "/redfish/v1/…": "_redfish_v1_….json" }` — every value is a file
  present in the same host dir; every resource JSON has a URL entry.
- `allowed_methods_mapping`: `{ "/redfish/v1/…": ["GET","HEAD","PATCH", …] }` — the real
  discovered HTTP methods, preserved exactly; a writable endpoint is **never** collapsed
  to `GET`/`HEAD`. Every URL here exists in `url_file_mapping`.

## Validation gate (fail closed)
`pack_full_corpus.py` refuses to write unless: all JSON parse; the map loads and has both
mappings; `json_file_count == len(url_file_mapping) == len(allowed_methods_mapping)`;
every mapped file exists; no resource JSON is unmapped; every methods-URL is in
`url_file_mapping`. `tests/test_full_corpus_contract.py` pins all of this.

`corpus_manifest.json` records `schema_version`, `artifact_type`, vendor/model/host_id,
`redfish_version`, the three counts, per-method `method_counts`, `redaction_status`, and
`artifact_checksum`.
