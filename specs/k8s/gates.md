# Kubernetes controller gates and operation contract

Operator-defined contract (2026-07-17) for the NodeProfile operation state machine
(issue #258) and its admission surface. Companion to `specs/telemetry/gates.md`;
the same rule applies: contract tests assert this document against the code, and
drift in either direction blocks merge.

## Operation status schema

```yaml
status:
  observedGeneration: 4
  operationId: "4d2f..."
  phase: applying
  planHash: "sha256:..."
  attempt: 2

  currentStep:
    index: 1
    kind: bios_profile
    state: request_sent
    requestFingerprint: "sha256:..."
    startedAt: "2026-07-17T10:10:00Z"

  bmcTask:
    uriHash: "sha256:..."
    state: Running
    percentComplete: 40

  conditions:
    - type: Approved
      status: "True"
      reason: PlanHashApproved
    - type: NodeQuiesced
      status: "True"
      reason: DrainCompleted
    - type: Verified
      status: "False"
      reason: ApplyInProgress
```

Notes that are part of the contract:

* `requestFingerprint` and `bmcTask.uriHash` are **hashes** — raw BMC URIs,
  addresses, and payloads never appear in status (bounded cardinality, no
  identifier leakage into etcd).
* `currentStep.state` (`pending → request_sent → task_running → confirmed |
  failed`) is persisted **before** the side effect it announces, the same
  write-ahead rule that closes the #255 replay window.
* Conditions are projections of `phase` + step state, never an independent
  source of truth.

## Step kinds (v1 — each maps to an existing guarded redfish_ctl command)

| kind | command seam | class | BMC task follow |
| ---- | ------------ | ----- | --------------- |
| `bios_profile` | bios-profile apply / change-bios (+ bios-reset for baseline) | staged, takes effect on reboot | yes (vendor job/task) |
| `boot_one_shot` | boot-one-shot | reversible one-time | no |
| `virtual_media` | vm-mount / InsertMedia / EjectMedia | reversible | no |
| `firmware_update` | firmware-update | irreversible-leaning; approval required | yes (long-running) |
| `power` | system-reset / power state (guarded) | disruptive; runs only inside an approved plan | no |
| `ntp` | ntp-set | reversible config | no |
| `identity` | identify-led / asset-tag-set | benign | no |
| `accounts` | account create/update/delete (test-user pattern only) | reversible | no |
| `subscription` | subscription-create / subscription-delete | reversible | no |
| `log_collect` | log-collect-diag | benign diagnostic | yes (202 task) |
| `verify` | read-only inventory/drift read | none | no |

**Forbidden step kinds — never expressible in a plan** (mirrors the standing
hard-no list): root/login password change, `Manager.Reset` / BMC reset /
factory reset, BMC certificate replacement, `Drive.SecureErase`, volume
delete. A plan containing one is rejected at admission, not at apply time.

## Gates

### K8S-G01 — API contract (merge)

The CRD has an explicit `phase`, `observedGeneration`, operation ID, `attempt`,
current step, task state, verification result, and terminal reason. Only
allowed state transitions are possible. Enforced by CRD schema tests plus
state-machine transition tests (full transition table + crash injection at
every persisted boundary — see #258).

Pass conditions:

```
schema-invalid status accepted        == 0
disallowed phase transitions accepted == 0
transitions without persisted intent  == 0
raw BMC URI/address fields in status  == 0
```

### K8S-G02 — Request/admission safety (merge)

An ordinary in-cluster caller can reference only an existing allowed
`RedfishEndpoint` and an approved profile. It cannot supply a raw BMC
address, an arbitrary Secret, an unbounded deadline, or an arbitrary
mutation payload. Enforced by admission tests for allowed and denied
callers, namespaces, endpoints, profiles, and CIDRs.

Pass conditions:

```
raw BMC address accepted           == 0
unreferenced/foreign Secret usable == 0
unbounded deadline accepted        == 0
free-form mutation payload accepted == 0
forbidden step kind accepted       == 0
cross-namespace endpoint reference == 0 (unless explicitly allowlisted)
```
