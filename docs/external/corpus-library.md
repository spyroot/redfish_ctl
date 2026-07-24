# Redfish Corpus Library

The corpus library is the shared set of captured Redfish trees ("corpora") that
back every offline surface in this project: the dual-mode unit tests, the mock
BMC server (`k8s/sandbox/mock_bmc_server.py --corpus-dir`), the fleet/proxy
tests, the request benchmarks, and the k8s sandbox. One capture per physical box
drives all of them, so the same JSON is reused everywhere instead of being
re-captured or forked per test.

Each corpus is a single Git-LFS `.tar.gz` under `tests/`, built by
`tools/pack_corpus.py` and indexed by `tests/corpus/manifest.json`. Consumers
that need the raw JSON extract a tarball rather than committing thousands of
loose files.

## What is in the library

| Vendor | Model | Redfish | JSON files | Surface |
| --- | --- | --- | ---: | --- |
| Dell iDRAC | PowerEdge XR8620t | 1.15.1 | 995 | full device + telemetry |
| HPE iLO 5 | ProLiant DL360 | 1.6.0 | 72 | curated read set |
| Supermicro | X10SDV-TLN4F | 1.6.0 | 61 | curated read set |
| Supermicro | GB300 | 1.17.0 | 1235 | full device + telemetry |
| NVIDIA / Supermicro | GB300 (node2) | 1.15.0 | 1646 | full device + telemetry |

That is **4009 JSON resources** across five boxes. The canonical index is
`tests/corpus/manifest.json`; this table mirrors it. Run `python tools/corpus.py list` to print the
current index as more boxes are added.

## Pull the entire corpus (all vendors, all JSON)

The tarballs are Git-LFS objects, so a fresh clone holds only pointers until they
are fetched. Downstream consumers that need the full JSON tree — for example an
ML/analytics pipeline that trains on every captured resource — run two steps:

```bash
# 1. Fetch every corpus tarball (LFS objects, not just pointers).
python tools/corpus.py pull

# 2. Materialize every corpus as JSON under one directory, grouped by vendor/model.
python tools/corpus.py extract-all --dest /path/to/redfish_corpus
```

`extract-all` writes one subdirectory per box:

```text
/path/to/redfish_corpus/
  dell_xr8620t/        # 995 *.json
  hpe_dl360/           #  72 *.json
  supermicro_x10sdv/   #  61 *.json
  supermicro_gb300/    # 1235 *.json
  nvidia_gb300-node2/  # 1646 *.json
```

`pull` wraps `git lfs pull --include=<tarballs>`, so `git lfs` must be installed
(`git lfs install` once per machine). To check the manifest and, when LFS objects are present, their
JSON counts:

```bash
python tools/corpus.py verify
```

`verify` reports `ok` per corpus when the tarball is present, or `pointer` for a tarball that has
not been pulled yet. It does not fail on un-pulled pointers, so the check is safe on a fresh
checkout; after `pull`, extraction-ready output should contain only `ok` lines.

### Pull or extract a single vendor

Every subcommand takes optional `--vendor` and `--model` filters:

```bash
python tools/corpus.py pull --vendor supermicro --model gb300
python tools/corpus.py extract-all --vendor dell --dest /tmp/dell_corpus
```

## Diff a corpus against a live BMC

`live-diff` compares one corpus against a live Redfish endpoint, read-only
(GET requests only, routed through the project client). Only STABLE
identity/config fields are compared — Manufacturer, Model, firmware versions,
boot allowable values, and the BIOS attribute key set — so volatile state
(power, sensor readings) never reports as drift. Resources are discovered from
the ServiceRoot collection links and their `Members` lists, so the same walk
covers every vendor's member naming:

```bash
python tools/corpus.py live-diff --vendor supermicro --model gb300 --ip <bmc-ip>
python tools/corpus.py live-diff --vendor dell --model xr8620t --ip <bmc-ip> --dry-run
```

`--dry-run` prints the fetch plan (the discovered resource paths) without
touching the network. The JSON report goes to stdout and diagnostics to stderr; the exit
code is 0 on match (gaps included), 1 on drift, 2 on a usage or environment
error. Credentials resolve from the gitignored inventory file or the
`REDFISH_USERNAME`/`REDFISH_PASSWORD` environment variables — never from argv.

`self-check` runs the same comparison engine with the corpus on BOTH sides —
no network, no credentials — which validates a newly added corpus walks
cleanly through the generic discovery:

```bash
python tools/corpus.py self-check                     # every pulled corpus
python tools/corpus.py self-check --vendor hpe        # one vendor
```

## Use a corpus from a test

Tests do not unpack tarballs by hand — they call the shared extractor
`tests/vendor_corpus.py::corpus_dir`, which extracts a tarball once per process
and caches it:

```python
from pathlib import Path
from tests.vendor_corpus import corpus_dir
from tools import corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
row = corpus.resolve("dell", "xr8620t")          # look a corpus up by vendor/model
d = corpus_dir(REPO_ROOT / row["tarball"], row["arcname"])  # -> dir of *.json
```

`corpus.resolve(vendor, model)` returns the manifest row (with `tarball` and
`arcname`), so tests locate a corpus by vendor/model instead of hardcoding the
capture path.

## Add a new capture

Adding the next box is a small, repeatable step:

1. Capture and **sanitize** the full tree following the canonical SOP in
   [fixture-capture.md](fixture-capture.md) — that document defines the crawl,
   the redaction checklist, and the schema-validation step. A full crawl carries
   device identifiers, so sanitization is mandatory; never commit a real
   credential or token.
2. Pack the sanitized tree into a tarball with `tools/pack_corpus.py`.
3. Add one row to `tests/corpus/manifest.json` (`vendor`, `model`,
   `redfish_version`, `json_count`, `tarball`, `arcname`, `source_note`).
4. Run `python tools/corpus.py verify` and `pytest -q tests/test_corpus_manifest.py`
   — the manifest count must match the tarball, and the tarball must be LFS-tracked.

## Notes

- Redfish never returns account passwords, so `Password` fields in a capture read
  `null`. The library carries device identifiers (serials, MACs, the capture host
  as each tarball's internal root) because captures come from decommissioned or
  lab gear; production captures must go through the redaction SOP first.
- The internal root of each tarball is currently the raw capture host. Resolving
  every consumer through the manifest (this document's `resolve()` path) is the
  first step of normalizing that root to a stable `vendor/model` slug; until that
  lands, `arcname` records the current root so lookups stay exact.
