# redfish_ctl

[![CI](https://github.com/spyroot/redfish_ctl/actions/workflows/ci.yml/badge.svg)](https://github.com/spyroot/redfish_ctl/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/redfish-ctl.svg)](https://pypi.org/project/redfish-ctl/)
[![Python versions](https://img.shields.io/pypi/pyversions/redfish-ctl.svg)](https://pypi.org/project/redfish-ctl/)
[![License: MIT](https://img.shields.io/pypi/l/redfish-ctl.svg)](https://github.com/spyroot/redfish_ctl/blob/main/LICENSE)

`redfish_ctl` is a standalone command-line tool for driving server BMCs entirely through the
Redfish REST API — no web UI, no vendor GUI. It wraps 100+ subcommands behind one consistent CLI
with JSON or YAML output (`--yaml`, and save-to-file), both synchronous and asynchronous calls,
optional server-side `$expand` on large collection reads, and safety labels that separate read-only,
guarded, and immediate-write operations. It is vendor-neutral by design — Dell iDRAC, Supermicro
(including GB300 / Grace-Blackwell and X10), HPE iLO, and generic DMTF Redfish — built on a
product-neutral Redfish client with the Dell/iDRAC specifics layered on top.

What it does across the whole server lifecycle:

- **Inventory & health** — system, chassis, manager, processors, memory, PCI, storage, drives,
  network adapters/ports, NVLink ports, ethernet interfaces, and firmware inventory.
- **BIOS** — read and stage attributes, pending management, the attribute registry, transactional
  snapshots/restore points for rollback, and curated tuning profiles (low-latency, Dell
  System/Workload, Intel, AMD).
- **Boot** — boot order, one-time boot (UEFI or Legacy), boot sources, and next-boot inference.
- **Power & reset** — vendor-neutral host reset / power-cycle (discovers `ComputerSystem.Reset`),
  chassis reset, manager reboot, and a guarded `system-reset`.
- **Storage & RAID** — controllers, drives, volumes, the RAID service, RAID/non-RAID conversion,
  and volume initialize.
- **Virtual media & OS provisioning** — mount/eject ISOs, one-shot ISO boot, Supermicro OEM
  virtual media (CfgCD), and Dell OEM network-ISO boot.
- **Serial console & SOL** — report and enable host BIOS serial redirection together with the BMC
  Serial-over-LAN service, in one step, vendor-neutrally.
- **Sensors & telemetry** — read every chassis sensor and TelemetryService report/definition, plus
  an out-of-band exporter that streams BMC metrics — including GB300 GPU, NVLink, thermal, and power
  — to Prometheus, SignalFx, and Splunk Observability. The
  [telemetry exporter guide](docs/external/telemetry-exporter.md) covers the one-exporter-per-BMC deployment
  model and the supported consumer modes.
- **Firmware** — inventory and guarded `UpdateService` SimpleUpdate.
- **Accounts & security** — create/update/delete accounts, SSH-key import, the account and privilege
  services, Secure Boot, and SPDM component-integrity attestation.
- **Jobs & tasks** — Dell Lifecycle Controller jobs and the standard Redfish Job/Task services,
  with watch/apply/delete.
- **Config, logs & events** — system config export/import, system and manager logs (SEL), test
  events, the BMC clock, and a `wait` that blocks until the BMC answers after a reboot.
- **Discovery** — scan a subnet for BMCs, classify their vendor, and crawl a Redfish tree.

> The tool was renamed from `idrac_ctl` to `redfish_ctl`. `idrac_ctl` still works as a
> backward-compatible alias — the `idrac_ctl` command, `import idrac_ctl`, and the legacy
> `IDRAC_IP`/`IDRAC_USERNAME`/`IDRAC_PASSWORD`/`IDRAC_PORT` env vars all keep working.
> When both `REDFISH_*` and legacy `IDRAC_*` names are set for the same value, they must match;
> different values fail closed so automation does not silently target the wrong BMC.

Author: Mus <spyroot@gmail.com>

## Quick start

```bash
# 1. Install (Python 3.10+)
python -m pip install redfish_ctl

# 2. Point it at a BMC (once per shell)
export REDFISH_IP=10.0.0.42
export REDFISH_USERNAME=root
export REDFISH_PASSWORD='your-password'

# 3. Read something safe
redfish_ctl --version          # prints the installed version
redfish_ctl system             # host ComputerSystem (Id, Name, PowerState)
redfish_ctl sensors            # temperatures, power, fans, voltages
redfish_ctl system --yaml      # same data as YAML instead of JSON
redfish_ctl --help             # every subcommand
```

Reads are safe. Commands that change hardware are labeled **Guarded** or **Write** in the
[command reference](docs/external/commands.md#registered-commands): Guarded commands require an explicit
intent flag such as `--confirm`; Write commands can mutate immediately and require explicit target
approval before live use. Preview with `--show` or `--dry_run` when the command supports it, then
see [Mutating Commands](#mutating-commands) below before applying anything.

> **Upgrading from `idrac_ctl`?** Install `redfish_ctl` — the `idrac_ctl` command, `import idrac_ctl`,
> and the legacy `IDRAC_*` env vars all keep working as a backward-compatible alias. The old
> `idrac_ctl` PyPI package (≤ 1.0.13) is the pre-rename tool; new work should `pip install redfish_ctl`.

## Install

Use Python 3.10 or newer.

```bash
python -m pip install redfish_ctl
redfish_ctl --version
```

For local development, use the checked-in conda environment:

```bash
git clone https://github.com/spyroot/redfish_ctl.git
cd redfish_ctl
conda env create -f environment.yml
conda activate redfish_ctl
```

## Run with Docker

The production image is defined in [docker/Dockerfile](docker/Dockerfile). It installs
`redfish_ctl[otlp]`, runs as a non-root user, and uses `redfish_ctl` as the entrypoint. Build it
locally from the repository root:

```bash
make docker-image IMAGE=redfish-ctl:local
```

Put BMC connection settings in `.internal/redfish.env`, a gitignored runtime file you create before
running the container:

```bash
mkdir -p .internal
cat > .internal/redfish.env <<'EOF'
REDFISH_IP=192.0.2.10
REDFISH_USERNAME=root
REDFISH_PASSWORD=change-this-password
REDFISH_PORT=443
EOF
```

Run a safe one-shot read:

```bash
docker run --rm --env-file .internal/redfish.env redfish-ctl:local system
```

Run the exporter as an OTLP sidecar:

```bash
docker run --rm \
  --env-file .internal/redfish.env \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
  redfish-ctl:local exporter --output otlp --interval 30
```

With Docker Compose, keep credentials in `.internal/redfish.env` and pass only collector routing in
the service definition:

```yaml
services:
  redfish-exporter:
    image: redfish-ctl:local
    env_file:
      - .internal/redfish.env
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
    command: ["exporter", "--output", "otlp", "--interval", "30"]
```

In Kubernetes, store BMC credentials in a Secret that you create in the target namespace, then point
the container at your collector:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: redfish-exporter
spec:
  containers:
    - name: exporter
      image: redfish-ctl:local
      imagePullPolicy: Never
      args: ["exporter", "--output", "otlp", "--interval", "30"]
      envFrom:
        - secretRef:
            name: redfish-bmc-credentials
      env:
        - name: OTEL_EXPORTER_OTLP_ENDPOINT
          value: http://otel-collector.monitoring.svc:4317
```

The Docker targets build and run locally only; they do not upload images or include credentials.
See [Docker Images](docker/README.md) for the image contract and Linux test image.

## Connect

The CLI reads these environment variables in `redfish_main.py`; set them once per shell:

```bash
export REDFISH_IP=10.0.0.42
export REDFISH_USERNAME=root
export REDFISH_PASSWORD='your-password'
export REDFISH_PORT=443
```

Any of these can be overridden per-invocation by a CLI flag. The canonical flags are
`--host`, `--username`, `--password`, and `--port`; the legacy aliases
`--idrac_ip`, `--idrac_username`, `--idrac_password`, and `--idrac_port` still work for
existing scripts.

BMCs usually ship self-signed certificates. TLS verification is off by default; use `--verify-ssl`
only when the BMC has a certificate chain you trust.

## First Safe Read

Start with the host ComputerSystem:

```bash
redfish_ctl system
```

A healthy response includes `data.Id`, `data.Name`, and usually `data.PowerState`. If you have `jq`
installed, this is a compact smoke check:

```bash
redfish_ctl --nocolor system | jq '.data | {Id, Name, PowerState}'
```

## Common Reads

```bash
redfish_ctl manager
redfish_ctl chassis
redfish_ctl sensors
redfish_ctl firmware_inventory
redfish_ctl bios --filter ProcCStates,SysMemSize
redfish_ctl storage-list
redfish_ctl get_vm
redfish_ctl logs
```

`sensors`, defined in `redfish_ctl/sensors/cmd_sensors.py`, follows Chassis sensor links and returns
temperature, power, fan, and voltage readings with units. `discovery`, defined in
`redfish_ctl/discovery/cmd_discovery.py`, is the heavier crawl that records what a BMC exposes.

## Vendor Reach

Dell iDRAC is the main control target. Vendor-neutral reads are fixture-backed for Dell iDRAC,
Supermicro GB300/X10, HPE iLO, and generic DMTF Redfish corpora, with HPE also covered by the
opt-in emulator canary in `examples/hpe_ilo_canary.sh`. See [Vendors](docs/external/vendors.md) and the
[Corpus Library](docs/external/corpus-library.md) for exact coverage.

## Mutating Commands

Some commands change real hardware: power, BIOS, boot order, storage conversion, virtual media,
firmware update, and BMC manager reboot (`manager-reboot`). Read current state first, preview when
the command has `--show` or `--dry_run`, then verify after the job or task completes.

```bash
redfish_ctl system-reset --reset_type GracefulRestart --dry_run
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
redfish_ctl firmware-update --image_uri https://example.invalid/firmware.exe --dry_run
```

Use `--confirm` only when you mean to perform a guarded action such as `system-reset` or
`firmware-update`.

## Observability with Splunk

`redfish_ctl` streams what it does to Splunk Observability APM (or any OTLP backend) so a fleet of
BMCs — and the operations run against them — are visible with no agent on the host and no code in the
firmware. Full guide: [Observability](docs/external/observability.md).

### Telemetry (metrics)

The exporter turns out-of-band hardware state (power, thermal, fans, GPU, leak detection, fabric)
into the stable `hw.*` metric family and pushes it over native OTLP. One exporter streams one BMC;
scale by running more. See [Telemetry Exporter](docs/external/telemetry-exporter.md).

```bash
redfish_ctl exporter --output otlp --once
```

### Traces and spans

Every command opens an operation span (`bios-change`, `firmware-update`, `reboot`), and every BMC
call becomes a `CLIENT` span tagged `peer.service=bmc` — so the fleet renders in APM as a
`redfish-ctl → bmc` service map with a trace waterfall, and per-operation error rate and latency in
Tag Spotlight (sliceable by vendor, action, and profile). Failed writes show up red, so a failed BIOS
apply or a slow firmware flash is one glance away.

### Quick start — up and streaming in three commands

```bash
pip install "redfish-ctl[otlp]"
export OTEL_EXPORTER_OTLP_ENDPOINT="https://<collector-or-ingest>:4317"
export OTEL_EXPORTER_OTLP_HEADERS="X-SF-Token=<splunk-access-token>"   # token via env, never argv
redfish_ctl --otlp-traces system      # appears in APM as redfish-ctl → bmc
```

For Kubernetes (one exporter pod per BMC, the operator reconciling profiles, all streaming to an
in-cluster Collector) see the [Kubernetes guide](k8s/README.md) and the Helm chart under `charts/`.

## Troubleshooting

First-run problems are almost always the connection, not the command:

- **`AuthenticationFailed` / HTTP 401** — wrong username or password, or an empty `REDFISH_PASSWORD`.
  Re-check the three env vars; many BMCs also lock the account after repeated failures.
- **TLS / self-signed certificate errors** — expected on most BMCs. TLS verification is *off* by
  default, so this usually means you passed `--verify-ssl` against a BMC without a trusted chain;
  drop the flag, or point it at a BMC whose certificate you trust.
- **Connection timeout / refused / no route** — the BMC IP is unreachable, on a different network,
  or Redfish is on a non-default port. Confirm reachability (`ping`, `curl -k https://$REDFISH_IP/redfish/v1`)
  and set `REDFISH_PORT` if it isn't 443. After a reboot, `redfish_ctl wait` blocks until the BMC answers again.
- **A command exists but returns little on your hardware** — Redfish trees differ by vendor and
  model. Use `redfish_ctl discovery` to see what your BMC actually exposes.
- **More detail** — add `--debug` (or `--verbose`) to any command to see the Redfish requests and
  responses behind it.

## More Docs

- [Command reference](docs/external/commands.md) - registered subcommands and safe workflow patterns.
- [Examples](examples/README.md) - one-line index of every script under `examples/`.
- [BIOS profiles](docs/external/bios-profiles.md) - low-latency, Dell System Profile, custom, Intel, and AMD
  profile examples.
- [Vendors](docs/external/vendors.md) - Dell, Supermicro, HPE, and generic Redfish support.
- [Observability](docs/external/observability.md) - stream BMC operations to Splunk APM as traces and metrics.
- [Telemetry Exporter](docs/external/telemetry-exporter.md) - the `hw.*` metric exporter and deployment model.
- [Simulation and replay](docs/external/simulation-and-replay.md) - the hardware-free mock and mutation replay.
- [Testing](docs/external/testing.md) - offline mock tests, vendor corpora, emulator tests, and live-test safety.
- [Corpus library](docs/external/corpus-library.md) - manifest-indexed Redfish corpus tarballs and pull-all extraction.
- [Docker](docker/README.md) - production image and Linux offline-test image usage.
- [Fixture capture](docs/external/fixture-capture.md) - crawl a BMC with `discovery`, sanitize it, and contribute it as a vendor corpus.
- [CI/CD](docs/external/ci.md) - the GitHub Actions test + release pipeline, the runner, and the Node.js runtime.
- [Architecture](docs/external/architecture.md) - Redfish core, iDRAC layer, command registration, and known debt.
- [Telemetry metrics](docs/external/telemetry-metrics.md) - GB300 MetricReport/MetricReportDefinition reference catalog.
- [Changelog](CHANGELOG.md) - what each release adds, changes, and fixes; watch **Unreleased** for what the next tag will contain.
- [Releasing](docs/external/releasing.md) - local verification, package build, PyPI upload, and tagging.
- [Fleet proxy design](docs/external/redfish-proxy.md) - planned service/controller shape for fleet management.
- [Scaling and benchmarks](docs/external/scaling-and-benchmarks.md) - planned concurrency engine and benchmark goals.
