# redfish_ctl

`redfish_ctl` is my command-line tool for talking to Dell iDRAC and other Redfish BMCs. I use it for
JSON-first inventory, BIOS, boot, storage, virtual media, sensors, logs, firmware, and job workflows
without opening the BMC web UI.

> The tool was renamed from `idrac_ctl` to `redfish_ctl`. `idrac_ctl` still works as a
> backward-compatible alias — the `idrac_ctl` command, `import idrac_ctl`, and the legacy
> `IDRAC_IP`/`IDRAC_USERNAME`/`IDRAC_PASSWORD`/`IDRAC_PORT` env vars all keep working.

Author: Mus <spyroot@gmail.com>

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

## Connect

The CLI reads these environment variables in `idrac_main.py`, so I set them once per shell:

```bash
export REDFISH_IP=10.0.0.42
export REDFISH_USERNAME=root
export REDFISH_PASSWORD='your-password'
export REDFISH_PORT=443
```

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

Dell iDRAC is the main control target. Supermicro GB300, HPE iLO, and generic DMTF Redfish trees are
covered by offline fixture corpora, with HPE also covered by the opt-in emulator canary in
`examples/hpe_ilo_canary.sh`. The current support matrix is in [Vendors](docs/vendors.md).

## Mutating Commands

Some commands change real hardware: power, BIOS, boot order, storage conversion, virtual media,
firmware update, and manager reset. I always read current state first, preview when the command has
`--show` or `--dry_run`, then verify after the job or task completes.

```bash
redfish_ctl system-reset --reset_type GracefulRestart --dry_run
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
redfish_ctl firmware-update --image_uri https://example.invalid/firmware.exe --dry_run
```

Use `--confirm` only when you mean to perform a guarded action such as `system-reset` or
`firmware-update`.

## More Docs

- [Command reference](docs/commands.md) - registered subcommands and safe workflow patterns.
- [Examples](examples/README.md) - one-line index of every script under `examples/`.
- [BIOS profiles](docs/bios-profiles.md) - low-latency, Dell System Profile, custom, Intel, and AMD
  profile examples.
- [Vendors](docs/vendors.md) - Dell, Supermicro, HPE, and generic Redfish support.
- [Testing](docs/testing.md) - offline mock tests, vendor corpora, emulator tests, and live-test safety.
- [Architecture](docs/architecture.md) - Redfish core, iDRAC layer, command registration, and known debt.
- [Telemetry exporter](docs/telemetry-exporter.md) - BMC metrics for Prometheus and SignalFx.
- [Telemetry metrics](docs/telemetry-metrics.md) - GB300 MetricReport/MetricReportDefinition reference catalog.
- [Releasing](docs/releasing.md) - local verification, package build, PyPI upload, and tagging.
- [Fleet proxy design](docs/redfish-proxy.md) - planned service/controller shape for fleet management.
- [Scaling and benchmarks](docs/scaling-and-benchmarks.md) - planned concurrency engine and benchmark goals.
