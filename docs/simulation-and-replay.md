# Simulation, Mutation Replay, and the RL Training Environment

Author: Mus <spyroot@gmail.com>

`redfish_ctl` ships a hardware-free way to exercise the full Redfish surface — reads *and* writes —
against captured real-hardware behavior. This exists for three reasons: fast offline tests, an
end-to-end Kubernetes sandbox with no BMC, and a deterministic Redfish environment that a
reinforcement-learning agent can train against. The three layers below build on each other.

## Layer 1: corpus as simulator (reads)

A full `discovery` crawl of a real BMC is committed as the filtered Git-LFS tarball
`tests/supermicro_gb300_corpus.tar.gz` (packed by `tools/pack_corpus.py`, with smaller vendor sets
alongside it). Every URL maps to one flat `_redfish_v1_..._.json` file inside it. Offline tests replay
this tree through `requests_mock`, and the sandbox mock BMC (`k8s/sandbox/mock_bmc_server.py`) serves
it read-only over HTTP. A new read command needs a thin behavioral test, not a bespoke fixture —
the captured tree is the simulator.

The same crawl output (`~/.json_responses/<ip>/` JSON dumps plus the `rest_api_map.npy` API map
written by `redfish_ctl discovery`) is the observation and schema source the `igc` project consumes
to learn the Redfish surface. See the discovery command in `redfish_ctl/discovery/cmd_discovery.py`.

## Layer 2: foreign simulator backends (protocol coverage)

The corpus reproduces one vendor's tree faithfully but not other implementations' protocol quirks
(session auth, ETags, link-only navigation, pagination, OEM shapes). The Kubernetes sandbox can run
alternate Redfish backends so the whole controller path is traced against foreign implementations.
The backend set is selected with `SANDBOX_BACKENDS` (see `k8s/sandbox/run-sandbox.sh`):

- `corpus-mock` — the committed capture served by the mock BMC (default).
- `ilo-sim` — a real HPE iLO Redfish emulator (`docker/Dockerfile.ilo-sim`), serving an HPE tree.

Additional backends (the DMTF Redfish-Interface-Emulator and Dell's published iDRAC mockups) are
planned so the matrix spans several independent Redfish services with no hardware.

## Layer 3: stateful mutation replay (writes)

Reads alone cannot exercise a mutation. Layer 3 teaches the mock BMC to accept writes and flip the
served state exactly as observed on real hardware, driven by a **trace** captured from a mutation
that was already verified live. This turns a static corpus into a *mutate-able* environment.

The mock BMC enters replay mode when started with a trace file. In the container image, the
committed GB300 corpus is mounted at `/corpus/gb300`:

```bash
python /app/mock_bmc_server.py --corpus-dir /corpus/gb300 \
  --replay tests/write_traces/graceful_restart.yaml
```

In replay mode the server still serves every corpus read, but a matched write applies an in-memory
overlay so subsequent reads of the affected resource return the new value. Reads never fall out of
the corpus; only the written fields change.

### Trace format

A trace lives under `tests/write_traces/` and is a JSON-compatible YAML document. Each step is a
match rule plus a response plus the state change the real BMC made. The committed
`graceful_restart.yaml` is the exact GB300 `ComputerSystem.Reset` verified on hardware:

```yaml
scenario: graceful_restart
description: Host ComputerSystem.Reset with ResetType=GracefulRestart updates LastResetTime.
steps:
  - name: host_graceful_restart
    method: POST
    path: /redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset
    body: {ResetType: GracefulRestart}         # match rule: method + path + body
    response: {status: 204}                     # answer exactly as the real BMC did
    state_transitions:                          # then flip the served value
      - op: set
        path: /redfish/v1/Systems/System_0
        field: LastResetTime
        value: "2026-04-14T23:35:33+00:00"
```

Fields:

- **match** — `method`, `path`, and optional `body` (subset match; extra request keys are ignored).
- **response** — `status` (and optional body) returned for the matched write.
- **state_transitions** — an ordered list of `op`s applied to the overlay. Supported ops: `set`
  (write a field on a resource) and `delete` (remove a field). A staged-then-applied change (for
  example a BIOS setting that becomes live only after a reset) is expressed as two steps: the write
  step sets the pending value, and the reset step sets the applied value and clears the pending one.

Writes match **in recorded order** — step *N* matches only after step *N-1* has matched. This pins
the exact real-world sequence (stage → verify pending → reset → verify applied). Reads are unordered
and always available from the corpus.

### Completeness assertion

The mock exposes `GET /__replay_status`, returning the scenario name, how many steps matched, which
remain pending, and whether the scenario is `complete`. A test asserts the client drove the exact
recorded sequence with nothing missing or extra — so a code change that sends an unexpected write
fails even when the write itself "succeeds". Coverage lives in
`tests/test_k8s_mock_bmc_server.py`.

## Consuming this as a reinforcement-learning environment (igc)

The `igc` project trains reinforcement-learning agents that operate a BMC over Redfish — agents whose
**actions are mutations** (change a BIOS attribute, set boot order, reset, configure NTP). Training
such an agent against real hardware is slow, destructive, and non-reproducible. The three layers
above are, together, a Redfish **environment** an agent can train against with none of those costs:

| RL concept | Provided by |
| --- | --- |
| Observation / state | Corpus reads (Layer 1) — the same JSON + `rest_api_map.npy` surface `igc` already ingests |
| Action space | Redfish writes (POST/PATCH/DELETE) the mock accepts in replay mode (Layer 3) |
| Transition dynamics | The overlay flip from a trace's `state_transitions`, captured from real hardware |
| Episode / trajectory | The ordered trace sequence; `/__replay_status` reports progress and completion |
| Determinism & resettability | `ReplayState.reset()` returns the environment to its initial state between episodes |

Two properties make this trustworthy as training data rather than a toy:

1. **Grounded in real behavior.** A transition is not a guess about what Redfish *should* do — it is
   what a specific real BMC *did*, recorded from a live-verified mutation (the GracefulRestart trace's
   `LastResetTime` is the value the GB300 actually returned after the reset). An agent learns the real
   dynamics, including delayed effects like apply-on-reset.
2. **Stable, machine-parseable contract.** The trace schema (`scenario` / `steps` /
   `method` / `path` / `body` / `response` / `state_transitions[op,path,field,value]`) is a versioned
   interface, kept deliberately stable so downstream consumers do not break. It composes with the
   discovery-crawl contract that already feeds `igc` its observation and schema surface.

The intended loop is closed: `redfish_ctl discovery` generates the observation/schema corpus, the
mock replays real mutation dynamics as the environment, an `igc` agent trains offline against it,
and the trained policy then acts on real hardware through the same guarded `redfish_ctl` write paths
it learned against. Every guarded write in `redfish_ctl` previews by default and requires an explicit
`--confirm`, so a policy exercising the real fleet inherits the same safety gate used in training.

Hard guarantees for any consumer: the mock never proxies to real hardware (it only serves recorded
JSON and applies recorded overlays), and every committed trace is sanitized of identifiers before
commit, exactly like the corpus itself.

## Adding a trace

1. Perform the mutation once on approved hardware with `redfish_ctl` (dry-run first, then
   `--confirm`), capturing the request/response and the before/after reads.
2. Sanitize: remove real IPs, serials, MACs, service tags, and tokens; use the corpus's placeholder
   conventions.
3. Write `tests/write_traces/<scenario>.yaml` with the match rule, the observed response, and the
   `state_transitions` that reproduce the observed field changes (use two steps for staged-on-reset
   effects).
4. Add a focused test modeled on `test_mock_bmc_replay_graceful_restart_updates_system_state` that
   drives the sequence and asserts `/__replay_status` reports `complete`.

## Status

- **Done:** the replay engine, `/__replay_status`, the GracefulRestart trace, its test, and the
  `corpus-mock` + `ilo-sim` sandbox backends.
- **Planned:** traces for the remaining live-verified mutations (staged BIOS change with
  apply-on-reset, NTP set, one-time boot arm), and the DMTF and Dell iDRAC simulator backends for the
  foreign-implementation matrix.
