# Redfish Simulator Contract

This document freezes the current simulator surface before the simulator is
refactored into a public package. It describes the behavior that existing
tests, the Kubernetes sandbox, and downstream consumers can rely on today.

## Scope

`k8s/sandbox/mock_bmc_server.py`, the current simulator implementation, serves a
flattened Redfish corpus over HTTP and can optionally accept grounded writes.
It has two mutually exclusive write engines:

- `ReplayState`, created by the `--replay` argument in
  `k8s/sandbox/mock_bmc_server.py`, replays an ordered mutation trace from
  `tests/write_traces/`.
- `MutationRules`, created by the `--mutation-rules` argument in
  `k8s/sandbox/mock_bmc_server.py`, applies order-independent rules from
  `tests/mutation_rules/`.

Reads are always corpus-backed. A write may only be treated as supported when a
trace or rule file grounds the method, path, request body, response, and state
transition. A discovered Redfish Action in a corpus is not enough by itself.
Operations not listed in the matrix below are unverified.

## Current Runtime Surface

`_OverlayStore`, defined in `k8s/sandbox/mock_bmc_server.py`, owns the in-memory
overlays that both write engines merge into served corpus JSON. Its transition
vocabulary is `set` and `delete`, each targeting a Redfish resource path and a
field or `json_path`.

`ReplayState`, defined in `k8s/sandbox/mock_bmc_server.py`, accepts only the next
pending step from an ordered trace. An out-of-order write returns `409` with the
remaining pending steps. `reset()` clears matched steps and overlays when the
requested scenario matches.

`MutationRules`, defined in `k8s/sandbox/mock_bmc_server.py`, evaluates every
rule against each write. All matching rules apply, so independent side effects
compose. A write with no matching rule returns `409`. Seeded failure injection is
reproducible: the same seed produces the same failure sequence, and `reset()`
re-seeds the sequence.

`CorpusRequestHandler`, defined in `k8s/sandbox/mock_bmc_server.py`, exposes:

- `GET`, `HEAD`, and `OPTIONS` for corpus resources under `/redfish/v1`.
- When `rest_api_map.npy`, written by discovery and packed with the corpus,
  is present, reads honor `url_file_mapping`, `http_status_mapping`, and
  `error_file_mapping`: captured 403/404/405 responses return their original
  status and error envelope instead of being normalized to `200`.
- `GET /__replay_status`, which reports replay or mutation-rule status when a
  write engine is enabled.
- `POST /__set_scenario`, which calls the active engine's `reset()` and returns
  the reset status.
- `POST`, `PATCH`, `PUT`, and `DELETE` only when a write engine is enabled.
  Read-only mode returns `405` for mutating methods.

`run_server`, defined in `k8s/sandbox/mock_bmc_server.py`, is the local test
entrypoint used by the offline suite. It starts a `ThreadingHTTPServer` on a
requested host and port, then shuts it down on context exit.

## Sandbox And Emulator Entrypoints

`docker/Dockerfile.mock-bmc`, the mock-BMC image definition, copies
`k8s/sandbox/mock_bmc_server.py`, unpacks
`tests/supermicro_gb300_corpus.tar.gz` into `/corpus/gb300`, runs as non-root
UID `10001`, exposes port `8080`, and uses
`ENTRYPOINT ["python", "/app/mock_bmc_server.py"]`.

`k8s/sandbox/mock-bmc.yaml`, the single-node sandbox manifest, starts
`redfish-ctl-mock-bmc:local` with
`--mutation-rules /rules/supermicro_gb300.yaml`. The rule file is supplied from
a ConfigMap created from `tests/mutation_rules/supermicro_gb300.yaml`.

`k8s/sandbox/gb300-fleet.yaml`, the fleet sandbox manifest, starts a 36-pod
StatefulSet from the same mock-BMC image and overlays per-pod identity with
`MOCK_BMC_RACK` and `MOCK_BMC_SLOT`.

`docker/Dockerfile.ilo-sim`, the HPE iLO emulator image definition, builds the
HPE `ilo-redfish-emulator` source into a non-root Python container and uses
`ENTRYPOINT ["python3", "emulator.py"]`.

`k8s/sandbox/ilo-sim.yaml`, the iLO sandbox manifest, exposes the emulator over
HTTPS on port `8443`.

`tests/test_emulator_smoke.py`, the optional emulator smoke lane, is skipped
unless `REDFISH_EMULATOR_URL` is set. It validates the real Redfish client
against a local emulator, not live hardware.

## Grounded Artifacts

`tests/write_traces/graceful_restart.yaml`, the committed ordered trace, grounds
one Supermicro GB300 host reset: `ComputerSystem.Reset` with
`ResetType=GracefulRestart` returns `204` and updates `LastResetTime`.

`tests/mutation_rules/supermicro_gb300.yaml`, the Supermicro GB300 rules file,
grounds power reset, boot-source override, one-time boot revert, BIOS pending
apply-on-reset, and standard VirtualMedia insert/eject.

`tests/mutation_rules/supermicro_gb300_flaky.yaml`, the stochastic Supermicro
GB300 rules file, grounds seeded failures for reset and standard VirtualMedia
insert.

`tests/mutation_rules/nvidia_gb300_node2.yaml`, the second GB300 rules file,
grounds the same GB300 mutation classes plus SEL log clear.

`tests/mutation_rules/dell_xr8620t.yaml`, the Dell XR8620t rules file, grounds
power reset, boot-source override, one-time boot revert, BIOS pending
apply-on-reset, SEL log clear, and storage volume create/delete.

`tests/mutation_rules/hpe_dl360.yaml`, the HPE DL360 rules file, grounds power
reset, boot-source override, one-time boot revert, BIOS pending apply-on-reset,
and IML log clear.

`tests/mutation_rules/supermicro_x10.yaml`, the Supermicro X10 rules file,
grounds power reset, boot-source override, one-time boot revert, and system log
clear for the early Redfish corpus.

## Mutating Capability Matrix

Status values:

- `supported`: a committed trace or mutation-rule file grounds the operation.
- `unsupported`: the committed corpus/rule notes explicitly state the resource
  class is absent for that vendor/model.
- `unverified`: no committed trace or mutation-rule file grounds the operation.

| Vendor/model | Host reset | Boot override | BIOS pending apply | Standard VirtualMedia | Log clear | Storage volume |
| --- | --- | --- | --- | --- | --- | --- |
| Dell XR8620t | supported | supported | supported | unsupported | supported | supported |
| Supermicro GB300 | supported | supported | supported | supported | unsupported | unsupported |
| HPE DL360 | supported | supported | supported | unsupported | supported | unsupported |
| Supermicro X10 | supported | supported | unsupported | unsupported | supported | unsupported |
| NVIDIA GB300 node2 | supported | supported | supported | supported | supported | unsupported |

The matrix is intentionally conservative. For example, firmware update,
manager reset, NTP set, account mutation, and RAID operations outside Dell
storage volume create/delete remain `unverified` until a trace or rule file
grounds them.

## Freeze Tests

`tests/test_simulator_contract_freeze.py`, the RF-SIM-00 freeze test file, pins
the contract described here. It asserts ordered replay behavior, out-of-order
write rejection, mutation-rule composition, unmatched-write `409` responses,
scenario reset behavior, seeded failure reproducibility, runtime entrypoints,
and the capability matrix above.
