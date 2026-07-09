# idrac_ctl

`idrac_ctl` is my command-line tool for talking to Dell iDRAC and other Redfish BMCs. I use it for
JSON-first inventory, BIOS, boot, storage, virtual media, sensors, logs, firmware, and job workflows
without opening the BMC web UI.

Author: Mus <spyroot@gmail.com>

## Install

Use Python 3.10 or newer.

```bash
python -m pip install idrac_ctl
idrac_ctl --version
```

For local development, use the checked-in conda environment:

```bash
git clone https://github.com/spyroot/idrac_ctl.git
cd idrac_ctl
conda env create -f environment.yml
conda activate idrac_ctl
```

## Connect

The CLI reads these environment variables in `idrac_main.py`, so I set them once per shell:

```bash
export IDRAC_IP=10.0.0.42
export IDRAC_USERNAME=root
export IDRAC_PASSWORD='your-password'
export IDRAC_PORT=443
```

BMCs usually ship self-signed certificates. TLS verification is off by default; use `--verify-ssl`
only when the BMC has a certificate chain you trust.

## First Safe Read

Start with the host ComputerSystem:

```bash
idrac_ctl system
```

A healthy response includes `data.Id`, `data.Name`, and usually `data.PowerState`. If you have `jq`
installed, this is a compact smoke check:

```bash
idrac_ctl --nocolor system | jq '.data | {Id, Name, PowerState}'
```

## Common Reads

```bash
idrac_ctl manager
idrac_ctl chassis
idrac_ctl sensors
idrac_ctl firmware_inventory
idrac_ctl bios --filter ProcCStates,SysMemSize
idrac_ctl storage-list
idrac_ctl get_vm
idrac_ctl logs
```

`sensors`, defined in `idrac_ctl/sensors/cmd_sensors.py`, follows Chassis sensor links and returns
temperature, power, fan, and voltage readings with units. `discovery`, defined in
`idrac_ctl/discovery/cmd_discovery.py`, is the heavier crawl that records what a BMC exposes.

## Vendor Reach

Dell iDRAC is the main control target. Supermicro GB300, HPE iLO, and generic DMTF Redfish trees are
covered by offline fixture corpora, with HPE also covered by the opt-in emulator canary in
`examples/hpe_ilo_canary.sh`. The current support matrix is in [Vendors](docs/vendors.md).

## Mutating Commands

Some commands change real hardware: power, BIOS, boot order, storage conversion, virtual media,
firmware update, and manager reset. I always read current state first, preview when the command has
`--show` or `--dry_run`, then verify after the job or task completes.

```bash
idrac_ctl system-reset --reset_type GracefulRestart --dry_run
idrac_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
idrac_ctl firmware-update --image_uri https://example.invalid/firmware.exe --dry_run
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
