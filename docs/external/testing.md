# Testing

Author: Mus <spyroot@gmail.com>

Before trusting a change, clear any live iDRAC environment and run the offline suite:

```bash
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD pytest -q
ruff check <changed>
```

`REDFISH_IP`, `REDFISH_USERNAME`, and `REDFISH_PASSWORD` are read by the CLI and by the dual-mode test
fixture. If `REDFISH_IP` is still exported from real hardware work, `redfish_api` switches to live mode,
so unset those variables for the default suite.

## Which Lane To Use

**Mock lane, default.** `tests/conftest.py` builds `MockRedfishService` from the captured DMTF tree in
`redfish_ctl/json_responses/`. Dell-shaped gaps are overlaid from `tests/idrac_fixtures/`. The service
handles `GET`, `POST`, `PATCH`, `DELETE`, and action-style POSTs, so mutating command tests can stay
offline.

Use the `redfish_mock` fixture when you need an `RedfishManagerBase` wired to the mock, and
`redfish_service` when you need to inspect requests or state changes.

**Dual-mode lane.** `redfish_api`, defined in `tests/conftest.py`, runs the same test against the mock
by default and against approved hardware when `REDFISH_IP` is set. Tests that require hardware are
marked `@pytest.mark.live` and skip without that variable.

For approved live hardware only:

```bash
REDFISH_IP=<idrac> \
REDFISH_USERNAME=root \
REDFISH_PASSWORD=<password> \
pytest -q -m live
```

That keeps the variables scoped to one command. If you exported them earlier, unset them before
returning to the default suite.

**Vendor-aware mock lane.** `redfish_mock_factory`, defined in `tests/conftest.py`, overlays
`tests/<vendor>_fixtures/` on the DMTF base. The repo has four corpora now: Dell
(`tests/idrac_fixtures/`), Supermicro GB300 (`tests/supermicro_fixtures/`), HPE iLO
(`tests/hpe_fixtures/`), and generic DMTF (`tests/generic_fixtures/`).

Worked examples:

- `tests/test_vendor_portability.py` checks Supermicro system and manager discovery.
- `tests/test_hpe_vendor.py` and `tests/test_ilo_gap_batch*.py` check HPE iLO read paths.
- `tests/test_generic_vendor.py` checks the generic DMTF fallback corpus.
- `tests/test_discover.py` checks `classify_vendor()` for Dell, HPE, Supermicro, and generic roots.
- `tests/test_discover_ids.py` checks multi-member system/manager discovery.
- `tests/test_sensors.py` runs the generic `sensors` command against the Supermicro overlay.

**Emulator lane, opt-in.** `tests/test_emulator_smoke.py` targets an external `sushy-emulator --fake`
process through `REDFISH_EMULATOR_URL`. It is skipped by default and validates generic Redfish
transport, not Dell OEM paths.

```bash
python -m pip install sushy-tools
sushy-emulator --fake -i 127.0.0.1 -p 8000
REDFISH_EMULATOR_URL=http://127.0.0.1:8000 pytest tests/test_emulator_smoke.py
```

## Fixtures And Faithfulness

The captured DMTF tree is generic. Dell-only resources belong in `tests/idrac_fixtures/`, and
non-Dell overlays belong in `tests/<vendor>_fixtures/`. Supermicro coverage is fixture-derived from a
read-only GB300 observation. HPE coverage comes from the HPE iLO emulator corpus plus the optional
`examples/hpe_ilo_canary.sh` live-emulator flow.

## Mac/Linux Parity

`docker/run-tests.sh` builds a deps-only `ubuntu:24.04` image (the `.[dev]` dependency set, no source
copy) and runs the offline suite against the repo mounted at `/work`. Linux is case-sensitive and
macOS is not, so this catches fixture-path mistakes that can hide on a laptop. Because the image
holds only dependencies, code edits reuse the cached image — it rebuilds only when
`requirements.txt`/`setup.py` change, and the script prunes dangling layers after a rebuild. No
source files are baked into the image.

## Coverage

Coverage is not a default gate yet. When you need a local report, install `pytest-cov` in the active
conda environment and keep the live variables unset:

```bash
python -m pip install pytest-cov
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD pytest --cov=redfish_ctl
```

## Fleet And Concurrency Tests

Fleet/concurrency testing is roadmap. The planned proxy reconcile loop, bounded concurrency engine,
multi-server simulator, latency injection, and benchmark harness are not current default gates. See
[scaling-and-benchmarks.md](scaling-and-benchmarks.md) for the planned shape.
