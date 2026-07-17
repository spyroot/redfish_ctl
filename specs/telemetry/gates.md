# Telemetry and tracing merge gates

Operator-defined gate contract (2026-07-17). Every telemetry or tracing change merges only
through the gates below. Machine-readable companions: `span_contract.yaml` (span attribute
schema) and `expected_signals.yaml` (fixture-to-signal mapping). Contract tests assert these
documents against the code; drift in either direction blocks merge.

## Merge gates

| Gate | Scope | Pass condition |
| ---- | ----- | -------------- |
| G0 — Signal contract | merge | Every metric and span change is represented in a machine-readable contract. Generated documentation has no diff after regeneration. |
| G1 — High-value telemetry completeness | merge | Every fixture row for `Status.Health`, `Status.HealthRollup`, `Status.State`, `LinkDownReasonCode`, `EDPViolationState`, `PowerBreakPerformanceState`, and `LastResetType` produces the expected signal or an explicitly documented unsupported result. |
| G2 — Metric semantic correctness | merge | Name, unit, type, temporality, dimensions, boolean conversion, enum conversion, and OTLP Sum/Gauge classification match the contract across all three backends (Prometheus, SignalFx, OTLP). |
| G3 — HTTP trace coverage | merge | Every GET, POST, PATCH, DELETE, async request, Redfish Action, and firmware upload produces exactly one CLIENT request span. No new direct HTTP bypass is introduced (repository check detects direct `requests.*` calls outside the traced seams). |
| G6 — Lifecycle and flush | merge | Finished spans are exported on normal exit, command failure, interrupt, and termination. Shutdown does not hang beyond the configured budget. |
| G10 — Runtime regression | production canary | Scrape latency, BMC request count, exporter failures, dropped spans, and trace volume stay within the agreed canary budgets. |
| G11 — Documentation truth | merge | `observability.md` and the generated telemetry catalog contain only capabilities verified by contract tests. |

Merge-gate test infrastructure: an **in-memory OTLP span exporter** pytest fixture (no
collector, offline) is the assertion surface for every span gate; schema and cardinality
checks run against all three metric outputs.

## Mutating-call span gates (G3 details)

"Mutating" refers to the BMC HTTP writes (POST/PATCH/DELETE, Actions, firmware upload) —
span schemas themselves are static; forbidden keys are forbidden at all times, in every
scenario. Pass conditions for all mutating calls:

```
missing mutating spans       == 0
duplicate request spans      == 0
new raw HTTP bypasses        == 0
unhandled request exceptions == 0
```

Each span test asserts:

* Span kind is `CLIENT`.
* Span is a child of the current operation span.
* `peer.service` remains `"bmc"`.
* `server.address` or the agreed `bmc.ip` attribute is present.
* HTTP method and status are present.
* Exceptions and timeouts set the span error status.
* `redfish.action` is present only for Actions.
* No request or response body is captured.
* One physical BMC request produces one span — not one span in the helper and another in its caller.

Span attribute schema lives in `span_contract.yaml`; its pass conditions:

```
missing required keys    == 0
unknown attribute keys   == 0
forbidden keys           == 0
secret-pattern matches   == 0
```

## Trace shapes

Normal CLI operation:

```
Operation ROOT
└── redfish.bmc.request CLIENT
```

Action followed by a task:

```
Operation ROOT
├── Redfish Action CLIENT
└── Poll Task INTERNAL
    ├── task check CLIENT
    ├── task check CLIENT
    └── task check CLIENT
```

The `Poll Task` span carries a **link** to the initiating Action/POST span, even when the
poll is resumed from another context. Required poll attributes: `redfish.task.state`,
`poll.count`, `poll.interval_ms`, `poll.elapsed_ms`, `poll.terminal_state`. Required poll
scenarios: immediate completion, multiple state transitions, timeout, cancellation, BMC
error during polling, missing task URI, command process exiting before task completion.

Discovery:

```
Operation ROOT
└── Redfish Discovery INTERNAL
```

```
discovery summary spans  == 1
per-endpoint child spans == 0
```

Summary attributes: `requests.count`, `resources.discovered`, `resources.failed`,
`duration_ms`, `status_class.2xx_count`, `status_class.4xx_count`,
`status_class.5xx_count`, plus counts by capability class (read-only, patchable,
deletable, actions — i.e. how much of the discovered surface can mutate).

Fleet:

```
Fleet coordinator ROOT
├── link → Node A operation ROOT
├── link → Node B operation ROOT
└── link → Node C operation ROOT
```

For `N` nodes:

```
fleet coordinator roots      == 1
independent node roots       == N
node roots carrying link     == N
node roots with bmc identity == N
cross-node parentage         == 0
unexpected orphan roots      == 0
```

Kubernetes controllers:

```
k8s.redfish_endpoint.reconcile ROOT
└── redfish.bmc.request CLIENT

k8s.redfish_node_profile.reconcile ROOT
├── dry-run plan operation
└── approved apply operation
```

Controller root spans carry the bounded Kubernetes identity fields
`k8s.namespace.name`, `k8s.resource.name`, and `k8s.resource.kind`, plus
`server.address`. They do not record Secret data, request bodies, response
bodies, raw URLs, or query strings.

## Lifecycle gate (G6 details)

Required scenarios: successful command exit; command raising an exception;
`KeyboardInterrupt`; SIGTERM; exporter temporarily blocked; exporter throwing during
shutdown; setup called twice; tracing disabled.

```
finished spans lost on healthy exporter == 0
shutdown hangs                          == 0
duplicate provider registration         == 0
trace setup when disabled               == 0
```

A bounded CLI shutdown budget applies (initial default: 5 s flush timeout).

## Telemetry-specific gates

### M1 — Model categorical states as states, not arithmetic values

Prefer one-hot state metrics with a bounded label:

```
hw.component.health{health="ok"} 1
hw.component.health{health="warning"} 1
hw.component.health{health="critical"} 1
hw.component.health{health="unknown"} 1

hw.component.state{state="enabled"} 1
hw.fabric.link_down_reason{reason="peer_reset_event"} 1
hw.power.edp_violation_state{state="asserted"} 1
hw.power.break_performance_state{state="active"} 1
```

Gate:

* Normalized lowercase values.
* An explicit allowlist per label.
* Unknown vendor values map to `other` or `unknown` — they are not dropped.
* Raw free-form strings never become labels.
* Exactly one current state per component and scrape.
* Old states become stale naturally and are not emitted simultaneously.

> Supersedes the numeric mapping shipped in PR #245 (`hw.health` OK=1/Warning=0.5/
> Critical=0). Reshape before the first production push so the wrong contract never
> reaches a backend.

### M2 — High-priority gap closure

Covered source properties: `Status.Health`, `Status.HealthRollup`, `Status.State`,
`LinkDownReasonCode`, `EDPViolationState`, `PowerBreakPerformanceState`, `LastResetType`.

Every property has fixture rows in `expected_signals.yaml`, e.g.:

```yaml
source_property: Status.Health
input: Critical
expected:
  metric: hw.component.health
  labels:
    health: critical
  value: 1
```

The gate computes `expected signals − emitted signals` over the fixtures and requires
`missing expected signals == empty`.

### R1 — Exporter self-signals

```
hw.scrape.source.ok{source="thermal"} 0|1
hw.scrape.source.duration_seconds{source="thermal"}
hw.scrape.source.errors_total{source="thermal", error_class="timeout"}
hw.scrape.sources_attempted
hw.scrape.sources_succeeded
hw.scrape.sources_failed
hw.scrape.bmc_requests_total{method="GET", source="thermal", status_class="2xx"}
```

Gate: every listed self-signal is emitted each scrape cycle and covered by the same
fixture-driven expected-signal check as M2; a per-source failure changes
`hw.scrape.source.ok` for that source only.

### R2 — Push-loop survival

Conditions exercised: timeout → success; HTTP 500 → success; connection reset → success;
invalid permanent configuration.

Pass conditions:

* Transient failures do not terminate the loop.
* Subsequent pushes succeed.
* Retry behavior is bounded.
* Permanent configuration errors become visible and do not spin at full speed.
* One backend failure does not kill collection for another backend.

## CI documentation-truth checks (G0/G11)

```
documented-but-unimplemented attributes == 0
implemented-but-undocumented signals    == 0
stale generated-document diff           == 0
```

## First write-tracing release — non-negotiable blockers

1. 100% mutating transport coverage.
2. Zero secret or payload leakage.
3. Exactly one request span per physical call.
4. Correct operation-parent relationship.
5. Flush on success and failure.
6. No new direct HTTP bypass.
7. Bounded span names and templated paths.
