# Supermicro GB300 corpus

The Supermicro GB300 lab Redfish crawl (host `172.25.230.37`, copied from the
local `~/.json_responses` crawl cache on 2026-07-09) is committed as the filtered
Git-LFS tarball `tests/supermicro_gb300_corpus.tar.gz`, built by
`tools/pack_corpus.py`. Tests extract it on demand with
`tests/vendor_corpus.corpus_dir`, and the sandbox mock-BMC image unpacks it into
`/corpus/gb300`. Four non-empty account `Password` response fields were replaced
with `<redacted>` before commit.

The flat `tests/supermicro_fixtures/` directory remains the active mock overlay;
this corpus stays separate so its snapshots do not change mock request
resolution.

This directory also retains the downloaded IGC dataset metadata that shipped with
the crawl — `json_responses/dataset.json`, `json_responses/json_data.tar.gz`, and
its `.md5` — which the `igc` project consumes. The Redfish JSON itself now lives
in the tarball above, not under `json_responses/`.
