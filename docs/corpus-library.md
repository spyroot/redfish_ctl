# Redfish Corpus Library

`corpora/manifest.v1.json` is the authoritative corpus index. It separates two
artifact contracts:

- `mock` artifacts are filtered, flat, mock-ready tarballs for redfish_ctl
  tests, the HTTP mock BMC, ordered replay, mutation rules, Kubernetes sandbox,
  fleet identity simulation, and benchmarks.
- `dataset` artifacts are recursive, sanitized capture trees for IGC and other
  analytics consumers. They preserve captured schemas, registries, events, logs,
  telemetry, OEM resources, and the URL/file plus allowed-method map when it is
  available.

Do not point a mock server at a multi-capture dataset root. Materialize one
selected mock artifact into one flat leaf before launching the mock.

## Fresh Clone

```bash
git clone https://github.com/spyroot/redfish_ctl.git
cd redfish_ctl
git lfs install
python tools/corpus.py pull --kind mock
python tools/corpus.py pull --kind dataset
python tools/corpus.py verify --kind mock --require-materialized
python tools/corpus.py verify --kind dataset --require-materialized
```

`verify` without `--require-materialized` keeps fresh-checkout behavior: bare
Git-LFS pointers are reported as `pointer` but do not fail the command. IGC and
release checks should use `--require-materialized` after `pull`.

## Mock Artifacts

Mock artifacts extract to one flat leaf per capture:

```bash
python tools/corpus.py extract-all --kind mock --vendor supermicro --model gb300 --dest ./build/mock
python k8s/sandbox/mock_bmc_server.py --corpus-dir ./build/mock/supermicro_gb300
```

The leaf contains flattened files such as `_redfish_v1.json` and
`_redfish_v1_Systems_System_0.json`. Do not recursively merge multiple mock
captures into one directory; flattened basenames collide across vendors.

## Dataset Artifacts

Dataset artifacts keep a recursive capture layout:

```bash
python tools/corpus.py materialize --kind dataset --vendor dell --output ./build/corpus
```

The recovered Dell XR8620T dataset extracts as:

```text
build/corpus/
  dataset/
    dell_xr8620t_2023-06-17/
      corpus.json
      rest_api_map.v1.json
      rest_api_map.npy
      json_responses/
        _redfish_v1.json
        ...
```

`rest_api_map.npy` keeps the legacy NumPy contract with exactly
`url_file_mapping` and `allowed_methods_mapping`. `rest_api_map.v1.json` carries
the same keys with file paths relative to the materialized capture root.

IGC should prefer the manifest plus materialized dataset root:

```bash
python tools/corpus.py pull --kind dataset --vendor dell
python tools/corpus.py verify --kind dataset --vendor dell --require-materialized
python tools/corpus.py materialize --kind dataset --vendor dell --output ./build/corpus
python -m igc... \
  --corpus-manifest ./corpora/manifest.v1.json \
  --corpus-root ./build/corpus
```

Legacy `~/.json_responses/<capture-host>/` inputs remain a compatibility path
during migration.

## Current Artifacts

| ID | Kind | Archive | Resource JSON | Maps | Status |
| --- | --- | --- | ---: | --- | --- |
| `dell-xr8620t` | mock | `corpora/mock/dell_xr8620t.tar.gz` | 995 | no | active |
| `hpe-dl360` | mock | `corpora/mock/hpe_dl360.tar.gz` | 72 | no | active |
| `supermicro-x10sdv` | mock | `corpora/mock/supermicro_x10sdv.tar.gz` | 61 | no | active |
| `supermicro-gb300` | mock | `corpora/mock/supermicro_gb300.tar.gz` | 1235 | no | active |
| `nvidia-gb300-node2` | mock | `corpora/mock/nvidia_gb300-node2.tar.gz` | 1646 | no | active |
| `dell-xr8620t-dataset-2023-06-17` | dataset | `corpora/dataset/dell_xr8620t_2023-06-17.tar.gz` | 2466 | JSON + NPY | incomplete |

The Dell dataset was recovered from the IGC `datasets/orig` tree and sanitized
with `tools/redact_corpus.py`. It includes EventService, LogService, seven
`Entries`-named files, schemas, registries, telemetry, OEM resources, and a
portable method map. It is marked `incomplete` because the source crawl cannot
prove that every possible live log member was captured.

## Python Consumers

Use the package API when a consumer needs manifest metadata, checksums, or safe
archive extraction:

```python
from pathlib import Path
from redfish_ctl import corpora

rows = corpora.select_rows(vendor="dell", kind="dataset")
outputs = corpora.materialize(Path("build/corpus"), vendor="dell", kind="dataset")

for row in rows:
    for name in corpora.iter_json_files(row):
        print(row.id, name)
```

For simple file processing after materialization:

```python
from pathlib import Path

root = Path("build/corpus/dataset/dell_xr8620t_2023-06-17/json_responses")
for path in sorted(root.rglob("*.json")):
    process(path)
```

## Adding A Capture

1. Capture and sanitize the tree following [fixture-capture.md](fixture-capture.md).
2. For mock use, build a filtered flat artifact with `tools/pack_corpus.py`.
3. For dataset use, preserve `json_responses/`, `rest_api_map.v1.json`, and
   `rest_api_map.npy` when the method map exists.
4. Add one row to `corpora/manifest.v1.json`.
5. Run `python tools/corpus.py verify --kind <mock|dataset> --require-materialized`
   and `pytest -q tests/test_corpus_manifest.py`.
