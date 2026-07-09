# Supermicro GB300 raw crawl corpus

`tests/supermicro_gb300_corpus/json_responses/`, copied from the local
`/Users/spyroot/.json_responses` crawl cache on 2026-07-09, preserves the full
GB300 lab Redfish corpus for offline research and regression work. Four
non-empty account `Password` response fields were replaced with `<redacted>`
before commit.

The flat `tests/supermicro_fixtures/` directory remains the active pytest
fixture overlay. Keep this raw corpus separate so multi-host snapshots,
archives, and map artifacts do not change mock request resolution.

Current contents:

- 4043 copied files.
- 4035 JSON Redfish artifacts.
- Host snapshots under `172.25.230.37`, `192.168.254.120`,
  `192.168.254.119`, and `10.43.3.209`.
- Original snapshots under `orig/`, plus the downloaded `json_data.tar.gz`
  metadata and crawl map files.
