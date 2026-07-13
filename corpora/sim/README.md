# Sim Corpora

Sim corpora are derivative artifacts built from dataset captures for mock
servers, scenario replay, or bounded tests. They are never counted as dataset
artifacts in `corpora/manifest.v1.json`; add them with `kind: "sim"` only when a
derivation command and source dataset corpus id are documented.
