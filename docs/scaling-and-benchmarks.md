# Scaling And Benchmarks

Author: Mus <spyroot@gmail.com>

The design target is for `redfish_ctl` to grow past one server and drive roughly 1,000 BMCs to a
desired state with numbers that show it is fast, correct, and stable. This page defines the fleet
engine, simulator, and benchmark gate. Those runnable artifacts are not in the repository yet.

## What Exists Today

The managers already expose async request helpers: `api_async_get`, `api_async_post`,
`api_async_patch`, and `api_async_delete`. Job polling also honors `Retry-After` on Dell task/job
flows. Those pieces are the foundation for fan-out work, but they are not a fleet runner by
themselves.

The discover package has a small fake-async harness in tests around scanner behavior. It is useful
seed material for a simulator because it exercises Redfish reads without live hardware.

## Planned Concurrency Engine

A future operation such as "apply profile `rt-low-latency` to these 1,000 servers" would run as
bounded per-server pipelines:

- async Redfish input/output through the existing `api_async_*` helpers,
- a cap on simultaneous servers and in-flight requests,
- ordered per-server steps for changes that create jobs or require reboot,
- subnet-wide pacing plus capped transient-error backoff,
- resumable state so a retry changes only servers still off spec.

Today, only the per-request async helpers and Dell `Retry-After` polling behavior exist. The bounded
executor, rate limiter, per-server pipeline orchestrator, and resumable fleet state are planned.

## Planned Fleet Simulator

Real BMCs are scarce and fragile, and one external emulator represents one server. The planned
simulator is a local async Redfish service that presents many synthetic servers with captured-style
resource trees, configurable latency, transient failures, auth failures, and mutating actions for
power, boot, BIOS apply, and job creation.

`sushy-emulator --fake`, used by the opt-in `tests/test_emulator_smoke.py` lane, is still useful for
one generic server. It is not wired as a fleet benchmark gate.

## Metrics And Targets

Each benchmark would write a result under `reports/`, such as `reports/bench-fleet-1000.json`, so
regressions are visible.

| Metric | Target |
|---|---|
| Read throughput | 1,000 simulated servers complete in a few minutes at a sane concurrency cap. |
| Latency spread | p99 per-server completion stays within a small multiple of p50. |
| Transient failure recovery | With 10% injected transient failures, at least 99% converge after bounded retries. |
| Retry behavior | No unbounded retry storms and no process crash. |
| Correctness | Converged state matches the desired spec with zero spurious mutations. |
| Resource use | CPU and memory stay within the target container budget. |

The first useful benchmark is read-and-report. Mutating converge benchmarks come after the simulator
can model jobs and reboots.
