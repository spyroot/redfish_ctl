# Changelog

All notable changes to `redfish_ctl` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Consumers that embed `redfish_ctl`
or drive its corpus-backed simulation (for example an out-of-band RL training
environment) should watch the **Unreleased** section for what the next tag will
contain.

## [Unreleased]

Targets the next minor release (1.3.0). Everything below is already on `main`.

### Added

#### Simulation and reinforcement-learning environment
- **Corpus-backed mock BMC.** `k8s/sandbox/mock_bmc_server.py` serves a Redfish
  tree captured from real hardware (the committed Supermicro GB300 / NVIDIA HGX
  corpus) over HTTP with a per-pod identity overlay, so one image can stand in
  for many distinct BMCs.
- **Ordered write replay.** `--replay <trace>` accepts a fixed sequence of
  writes and overlays the resulting state onto later reads.
- **Order-independent mutation rules.** `--mutation-rules <vendor>.yaml` matches
  each write on `(method, path-pattern, precondition over current state)` rather
  than trace position, so a controller (or an RL agent) may drive mutations in
  any order and conditional effects compose (a reset both powers a system off
  and, only when a one-time boot is armed, reverts it). Ships with rules for the
  GB300 power / boot-override / one-time-boot / BIOS pending-and-apply / virtual
  media classes. See `tests/mutation_rules/` and `docs/simulation-and-replay.md`.
- **Stochastic failure injection.** A mutation rule may carry a `failure` block
  so the mock rejects a matching write with a configurable probability and
  applies no state change (a reboot that does not take, a media insert that
  fails). Failures are reproducible via a seed (`--seed` / `MOCK_BMC_SEED`), so
  an RL episode replays identically and varies its failures by choosing a
  different seed; rules with no failure block stay fully deterministic.

#### Kubernetes control plane and end-to-end sandbox
- **Two kopf controllers.** A read-only `RedfishEndpoint` status poller and a
  desired-state `RedfishNodeProfile` controller with a one-shot, plan-hash-gated
  apply (plan → operator approves the reported hash → apply once → converge).
- **kind end-to-end sandbox.** `make k8s-sandbox` builds the mock and controller
  images, runs them in a throwaway kind cluster, and asserts the full read and
  write reconcile paths — no real BMC, no credentials. Runs in CI (`k8s-e2e`).
- **Helm chart** (`charts/redfish-controller`) as an alternative install path,
  and an iLO-simulator sandbox backend for cross-vendor coverage.

#### Observability
- **OpenTelemetry traces.** `--otlp-traces` emits a CLIENT span per BMC request
  (with `peer.service="bmc"`), so a workflow of BMC operations renders as a set
  of correlated traces in an APM backend. Complements the existing `hw.*` OTLP
  metrics exporter.

#### Vendor portability
- Vendor capability report and conformance claims derived from ServiceRoot
  classification, a CSDL action-parameter validator, a Redfish schema-tree
  validator, a fixture catalog manifest, and a Lenovo XCC profile.

#### Commands
- `UpdateService` read, a guarded `WorkloadPower` action, guarded volume
  create/delete, and a firmware-update push-upload fallback.

### Fixed
- CLI parser crashed on Python 3.11+ when two commands registered the same
  argparse subcommand name (`compute-query`, `system-import`); renamed to
  `compute-update` / `system-onetime-boot` with a registry guard test.
- Both kopf controllers logged a merge-patch inconsistency every poll (a non-
  `None` handler return kopf tried to persist under a rejected status field).
- The Helm chart's node-profile CRD had drifted from the controller CRD, which
  would have broken gated mutations on a chart install; a guard test now pins
  the two together.
- Command singletons are keyed by BMC connection; failed writes are no longer
  reported as success; Redfish error parsing and enum comparisons hardened.

### Changed
- One shared ServiceRoot fetch with pinned request-count (round-trip) budgets.

## [1.2.0] - 2026-07-10
### Added
- Renamed the package to `redfish_ctl` (the `idrac_ctl` console script and
  import alias are retained). Published to PyPI and as a multi-arch container
  image on release tags.
- Native OTLP metrics exporter (`--output otlp`, `[otlp]` extra), a pooled
  Redfish session, and the fixture-capture SOP.

## [1.1.0] - [1.1.4]
- Initial `redfish_ctl` releases on PyPI with the `idrac_ctl` alias.

[Unreleased]: https://github.com/spyroot/redfish_ctl/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/spyroot/redfish_ctl/releases/tag/v1.2.0
