# Managing Bare-Metal Servers from Kubernetes

Author: Mus <spyroot@gmail.com>

This directory turns a Kubernetes cluster into the control plane for bare-metal
servers: every BMC becomes a Kubernetes object that can be read, watched,
streamed, and — behind an explicit approval gate — changed. Everything here
runs against the Redfish API out-of-band, so it works before an OS is
installed, while the host is wedged, and during provisioning.

Three pieces cooperate, each doing one job:

| Piece | Kind | Job |
| --- | --- | --- |
| `RedfishEndpoint` + controller (`redfish_endpoint_controller.py`) | CRD + read-only controller | keeps each BMC's live state (power, health, temperature) on the object's `.status` |
| `RedfishNodeProfile` + operator (`redfish_node_profile_controller.py`) | CRD + operator | computes drift between a node and its desired state; applies **only** when the object says `approve: true` |
| exporter pods (one per BMC) | Deployment(s) | stream each BMC's metrics to the cluster's OpenTelemetry Collector — see the [deployment model](../docs/telemetry-exporter.md#deployment-model-one-exporter-per-bmc) |

Credentials always come from a namespaced Secret via `secretRef` — never from
images or CRs. Every example below can be tried with **zero hardware** using
the bundled sandbox (`make k8s-sandbox`), which serves a full captured
Supermicro GB300 Redfish tree from a mock BMC.

## Scenario: watch fleet state at a glance

Register a BMC once, then the fleet becomes a `kubectl` query. The controller
polls each endpoint on its own timer and writes the summary to status:

```yaml
apiVersion: redfish.ctl.dev/v1alpha1
kind: RedfishEndpoint
metadata:
  name: rack7-node3
spec:
  address: 192.0.2.31
  port: 443
  insecure: true
  pollInterval: 30s
  secretRef:
    name: rack7-node3-bmc
```

```console
$ kubectl get redfishendpoints
NAME          POWER   HEALTH   POLLED
rack7-node3   On      OK       2026-07-11T02:10:41Z
```

A node that loses power or cooling shows up here without anyone SSH-ing
anywhere — the read path is the fleet's heartbeat.

`spec.pollInterval` sets each endpoint's cadence. The controller's kopf timer
fires at a base cadence — 30s by default, retunable per deployment via the
`REDFISH_CONTROLLER_POLL_INTERVAL` env var (the Helm chart renders it from
`controller.pollInterval`) — and each endpoint polls no more often than that
base; a larger `pollInterval` slows an individual endpoint down. When a BMC is unreachable the poll does not fail the
object: the controller records an `EndpointReachable=False` condition plus a
`lastError` and backs off (`nextPollAfter`) with exponential growth, while the
last good `powerState`/`health`/`temperature` and `lastPolled` are preserved.
Editing the spec (e.g. fixing a bad `address`) re-polls immediately rather than
waiting out the interval or backoff.

## Scenario: stream telemetry to the collector

For dashboards and alerting, status snapshots are not enough — run one
exporter pod per BMC and let the cluster's OpenTelemetry Collector merge the
streams. Each pod publishes the stable `hw.*` metric family (`hw.power`,
`hw.temperature`, `hw.gpu.*`, `hw.leak.state`, `hw.fabric.*`) with the node's
identity attached as resource attributes, so the Collector can route and
aggregate per rack, per row, or per fleet.

The GB300-class payoff: this telemetry is **out-of-band GPU observability** —
GPU power, temperature, throttle state, and NVLink counters read from the BMC
while the host has no OS, no driver, or no pulse. An in-band agent cannot do
any of this before boot; this path can.

## Scenario: tune a node for low latency

BIOS latency tuning (C-states off and friends) is exactly the kind of change
that should be reviewed before it happens. The `RedfishNodeProfile` operator
makes it a two-step: **see the plan, then approve it.**

```yaml
apiVersion: redfish.ctl.dev/v1alpha1
kind: RedfishNodeProfile
metadata:
  name: rack7-node3-lowlat
spec:
  endpoint:
    address: 192.0.2.31
    secretRef:
      name: rack7-node3-bmc
  desiredState:
    biosProfile: dell-cstates-off   # a named profile from specs/profiles/
  approve: false                    # plan only — nothing is written
```

The operator diffs the node's live BIOS attributes against the named profile
(catalogued with purpose and risk notes in [BIOS profiles](../docs/bios-profiles.md))
and writes the drift plan into `.status`. Nothing has touched the BMC beyond
reads. When the plan looks right:

```bash
kubectl patch redfishnodeprofile rack7-node3-lowlat \
  --type merge -p '{"spec":{"approve":true}}'
```

The apply path stages the change through the same guarded machinery the CLI
uses — a rollback snapshot of the current values is captured first, the
change stages as pending, and it takes effect on the next reset. Set
`waitForReboot: true` to have the operator confirm the BMC answers again
after an approved reboot step.

## Scenario: point the fleet at the right clocks

Wrong NTP on a fleet skews logs, tokens, and distributed traces. The same
profile object carries time configuration:

```yaml
  desiredState:
    ntp:
      servers: ["0.pool.ntp.org", "1.pool.ntp.org"]
```

The reconciler validates each server, refuses more than four, touches only the
NTP block of the manager's network protocol settings, and — like everything
else — previews by default and applies only under `approve: true`. This change
is non-disruptive: no host reboot, no BMC session drop.

## Scenario: controlled boot and restart

Provisioning flows need "boot from PXE once, then restart" without a human at
a console:

```yaml
  desiredState:
    boot:
      device: Pxe          # one-time boot override, cleared after use
    reboot:
      resetType: GracefulRestart
```

Both steps sit behind the same approval gate, and the reboot delegates to the
action-discovery path that validates the reset type against what the BMC
actually advertises. A failed write never escalates into a restart — error
responses stop the sequence and surface in `.status`.

## Scenario: manage BMC accounts

Break-glass credentials and per-operator accounts are managed through the
account commands (run one-shot, e.g. from a Job or workstation):

```bash
redfish_ctl account-create --username operator2 --password "$NEW_PASS" --role Operator          # dry-run
redfish_ctl account-create --username operator2 --password "$NEW_PASS" --role Operator --confirm
redfish_ctl account-import-sshkey --username operator2 --key-file id_ed25519.pub --confirm
```

Both commands preview by default and act only with `--confirm`; deleting the
account currently in use is refused. Directory-backed login (LDAP / Active
Directory on Supermicro OEM endpoints) is on the roadmap; local accounts and
SSH keys are the supported path today.

## Scenario (GB300 / NV72): know the silicon before the OS boots

The captured GB300 tree packed in `tests/supermicro_gb300_corpus.tar.gz` is a
faithful mirror of a real NVL system, and every command below runs against it in
the sandbox exactly as it runs against the real machine:

```bash
redfish_ctl gpu-metrics        # per-GPU power/temperature/clocks/memory, out-of-band
redfish_ctl nvlink-ports       # NVLink fabric: link state, speed, error counters
redfish_ctl leak-detectors     # liquid-cooling leak detection state per detector
redfish_ctl network-adapters   # NICs as the BMC sees them
redfish_ctl storage-drives     # drive bays, including which NVMe slots are populated
redfish_ctl firmware           # UpdateService firmware inventory
```

Real shapes from this tree: the NIC inventory reports an NVIDIA
**ConnectX-8 800GE 2P** on the IO board and a **BlueField-3** on the riser;
the firmware inventory lists per-GPU entries such as `HGX_FW_GPU_1` at version
`97.10.3E.00.05` with `Updateable: true`; the drive walk distinguishes
populated NVMe bays from `Absent` slots. None of this requires the host to be
up — which is the point: a provisioning pipeline can verify "right NIC, right
GPU firmware, right drives populated" before ever installing an OS, and a
liquid-cooled fleet can alert on `hw.leak.state` while a wedged host says
nothing.

## Scenario: firmware — inventory today, guarded update

Reading firmware state is free of risk and always available (`redfish_ctl
firmware`). Applying firmware is a real mutation with real consequences, so
the update command carries the full guard set:

```bash
redfish_ctl firmware-update --image_uri http://192.0.2.50/fw/bmc_4.02.bin --dry_run   # preview only
redfish_ctl firmware-update --image_uri http://192.0.2.50/fw/bmc_4.02.bin --confirm   # actually stages
```

Dry-run previews the exact request without sending it; `--confirm` is required
to act. Treat any firmware write as a maintenance-window operation against an
approved target. Fleet-level rollout orchestration (canary groups, health
checks between stages) is on the roadmap; the per-node guarded primitive is
what ships today.

## The safety model, in one place

- **Reads are always safe** and need no approval: status polling, telemetry,
  inventory.
- **Every write previews by default** — profile applies, NTP, boot, reboot,
  firmware all show their plan first and act only on `--confirm` (CLI) or
  `approve: true` (operator).
- **Failed writes never cascade** — an error response stops a sequence
  instead of proceeding to commit or reboot, and regression tests plus an
  AST-level check in CI enforce this shape.
- **Credentials live in Secrets** (or environment/credential files for the
  CLI), never in images, CRs, or argv.

## Try all of it with zero hardware

```bash
make k8s-sandbox
```

This builds the mock BMC (serving the GB300 corpus over HTTP), stands up a
throwaway kind cluster, deploys the CRDs and controller, and waits until a
sample `RedfishEndpoint` reports `powerState` — the same end-to-end path CI
runs on every change to this directory. Details in
[k8s/sandbox/README.md](sandbox/README.md).
