# Supermicro GB300 Corpus Metadata

The Supermicro GB300 Redfish JSON now lives in the full Git-LFS archive
`corpora/mock/supermicro_gb300.tar.gz`, indexed by `corpora/manifest.v1.json`.
Tests materialize it on demand through `tests/vendor_corpus.corpus_dir`, and the
sandbox mock-BMC image unpacks the same archive into `/corpus/gb300`.

This directory retains downloaded IGC dataset metadata that shipped with the
crawl: `json_responses/dataset.json`, `json_responses/json_data.tar.gz`, and its
`.md5`. The Redfish JSON itself is in the archive above, not under
`json_responses/`.
