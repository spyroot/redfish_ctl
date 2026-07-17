# Command Reference

Author: Mus <spyroot@gmail.com>

When connecting to a new BMC, run `redfish_ctl system` first. It proves the endpoint, credentials, and
basic Redfish path before deeper inventory or any staged change.

The table below follows the command names imported by `redfish_ctl/__init__.py`. Run
`redfish_ctl <command> --help` for flags on your installed version. (`idrac_ctl` remains a
backward-compatible alias for the `redfish_ctl` command, and `IDRAC_*` env vars are still read.)

## Connection Basics

`redfish_main.py` reads these environment variables when you do not pass explicit connection flags:

```bash
export REDFISH_IP=10.0.0.42
export REDFISH_USERNAME=root
export REDFISH_PASSWORD='your-password'
export REDFISH_PORT=443
```

TLS verification is off by default because lab BMCs commonly use self-signed certificates.
`--verify-ssl`, defined by the root parser in `redfish_main.py`, opts into certificate verification
when you have a trusted chain.

## Server-Side Query Flags

`redfish_main.py` defines top-level Redfish GET query flags that must appear before the subcommand:

```bash
redfish_ctl --select Id,Name query --resource /redfish/v1/Managers
redfish_ctl --top 5 query --resource /redfish/v1/Managers
redfish_ctl --expand --expand-levels 2 query --resource /redfish/v1/Systems
```

The supported flags are `--select`, `--filter`, `--expand`, `--expand-levels`, `--top`, and `--only`.
They are validated against the target vendor capability profile before any command GET is issued.
For example, Dell iDRAC profiles allow the standard Redfish query parameters but enforce iDRAC's
one-query-parameter-per-URI rule, while profiles that do not declare support reject the flag early.
These root flags are separate from command-specific filters such as `bios --filter`.

## First Reads

```bash
redfish_ctl system
redfish_ctl manager
redfish_ctl chassis
redfish_ctl sensors
redfish_ctl firmware_inventory
redfish_ctl bios --filter ProcCStates,SysMemSize
redfish_ctl logs
redfish_ctl accounts --usernames
redfish_ctl storage-list
redfish_ctl get_vm
redfish_ctl get /redfish/v1/Managers
```

`system` returns the host ComputerSystem. `manager` returns the BMC manager. `sensors`, defined in
`redfish_ctl/sensors/cmd_sensors.py`, follows Chassis sensor links and returns readings with units.
`logs`, defined in `redfish_ctl/logs/cmd_logs.py`, follows system and manager LogService entries.
`get`, defined in `redfish_ctl/cmd_get.py`, reads any Redfish resource URI when a dedicated command
does not exist yet.

## Registered Commands

Safety labels:

- **Read**: expected to read state only.
- **Guarded**: does not mutate by default; requires `--confirm` or an equivalent apply flag.
- **Write**: can mutate when invoked; do not run live without explicit target approval, even if an
  optional preview flag exists.

| Command | Purpose | Safety |
|---|---|---|
| `account` | Read one account resource. | Read |
| `account-create` | Create a Redfish account (ManagerAccount); requires `--confirm` to write. | Guarded |
| `account-delete` | Delete a Redfish account (self-delete guarded); requires `--confirm` to write. | Guarded |
| `account-import-sshkey` | Import or remove an account's authorized SSH key (HPE iLO OEM); requires `--confirm` to write. | Guarded |
| `account-update` | Update a Redfish account (role, password, enable); requires `--confirm` to write. | Guarded |
| `account-svc` | Read AccountService. | Read |
| `accounts` | Read the account collection; `--usernames` prints only usernames. | Read |
| `actions` | List Redfish actions exposed by the box and their risk levels. | Read |
| `asset-tag-set` | Read or set a chassis or system AssetTag; dry-run by default and `--confirm` applies. | Guarded |
| `attr` | Read manager attributes. | Read |
| `attr-clear-pending` | Clear pending manager attribute values. | Write |
| `attr-update` | Stage manager attribute changes. | Write |
| `bios` | Read BIOS attributes. | Read |
| `bios-change` | Stage BIOS attributes from a spec or attribute pair. | Write |
| `bios-clear-pending` | Clear pending BIOS values. | Write |
| `bios-pending` | Read pending BIOS values. | Read |
| `bios-profile` | List/show committed BIOS tuning profiles, diff against current BIOS attributes, or preview/apply a profile with `--confirm`. | Guarded |
| `bios-registry` | Read BIOS registry metadata, choices, and writable attributes. | Read |
| `bios-reset` | Preview or perform the Redfish BIOS resource's `Bios.ResetBios` action; requires `--confirm` to execute. | Guarded |
| `bios-snapshot` | Capture a BIOS restore point for rollback-able changes. | Read |
| `bmc-scan` | Scan a network segment for Redfish BMCs. | Read |
| `boot` | Read boot source data (vendor-neutral: falls back to the ComputerSystem `Boot` object). | Read |
| `boot-one-shot` | Set a one-time boot target; `--dry_run` previews the Boot PATCH payload. | Write |
| `boot-options` | Read boot option members. | Read |
| `boot-options-clear` | Clear pending boot option values. | Write |
| `boot-pending` | Read pending boot source values. | Read |
| `boot-settings` | Read current and pending boot settings. | Read |
| `boot-source` | Read a boot source, optionally with `--dev <device>`. | Read |
| `boot-source-enable` | Enable or disable a boot option member. | Write |
| `boot-source-registry` | Read boot source registry data. | Read |
| `boot-source-update` | Stage boot source settings. | Write |
| `boot-sources` | List boot source members. | Read |
| `boot-state` | Infer what the host will boot (target/order/media). | Read |
| `capability-report` | Export registered vendor capability profiles for IaC and inventory tooling. | Read |
| `certificates` | Read CertificateService and linked certificate inventory metadata without certificate bodies. | Read |
| `change-boot-order` | Change boot order and boot options. | Write |
| `cert-gen-csr` | Generate a CertificateService CSR through the `CertificateService.GenerateCSR` action; validates `--certificate-collection`; `--dry_run` previews without POSTing. | Write |
| `chassis` | Read chassis services. | Read |
| `chassis-reset` | Change chassis power state. | Write |
| `component-integrity` | Read ComponentIntegrity/SPDM attestation resources. | Read |
| `controls` | Read Chassis Controls collections, setpoints, ranges, and current readings from `redfish_ctl/controls/cmd_controls.py`. | Read |
| `compute-query` | Read ComputerSystem settings. | Read |
| `console-info` | Report serial, graphical, and shell console links per manager. | Read |
| `current_boot` | Read current boot source details. | Read |
| `dell-lc-svc` | Read Dell Lifecycle Controller service data. | Read |
| `discovery` | Recursively walk Redfish resources and record allowed methods. | Read |
| `eject_vm` | Eject virtual media. | Write |
| `environment-metrics` | Read linked EnvironmentMetrics resources for power, energy, temperature, and power-limit rollups. | Read |
| `ethernet-interfaces` | Read host and manager EthernetInterfaces. | Read |
| `event-service` | Read EventService, SSE filter support, and subscription collection summary. | Read |
| `event-submit-test` | Submit a Redfish test event; `--dry_run` previews the payload. | Write |
| `exporter` | Expose BMC telemetry as Prometheus text or SignalFx datapoints. | Read |
| `firmware` | Read firmware view data. | Read |
| `firmware-update` | Run UpdateService SimpleUpdate or a discovered push upload URI; `--dry_run` previews, `--confirm` writes. | Guarded |
| `firmware_inventory` | Read firmware inventory. | Read |
| `fleet` | Read a YAML fleet inventory and summarize per-node health, sensor count, and temperature max. | Read |
| `get` | Read an arbitrary Redfish resource URI. | Read |
| `get_vm` | Read virtual media. | Read |
| `gpu-metrics` | Read consolidated GPU temperature, compute, throttle, and memory metric rows. | Read |
| `hpe-test-actions` | List or send HPE iLO directory, SNMP, mail, and syslog test actions; dry-run by default and `--confirm` posts. | Guarded |
| `identify-led` | Read or set a chassis/system identify LED; requires `--confirm` to write. | Guarded |
| `insert_vm` | Insert virtual media from a URI. | Write |
| `job` | Read one Dell job. | Read |
| `job-apply` | Apply pending jobs. | Write |
| `job-rm` | Delete one job. | Write |
| `job-rm-all` | Delete all jobs. | Write |
| `job-watch` | Watch a job until it reaches a terminal state. | Read |
| `jobs` | Read the job collection. | Read |
| `jobs-dell-service` | Read Dell JobService. | Read |
| `jobs-service` | Read standard Redfish JobService. | Read |
| `leak-detectors` | Read chassis LeakDetection detector states and linked leak policy data. | Read |
| `license-install` | Install a license file through `LicenseService.Install`; lists the discovered action target when no URI is given, requires `--confirm` to write. | Guarded |
| `logs` | Read system and manager log entries. | Read |
| `log-clear` | Clear a discovered LogService (LogService.ClearLog); lists clearable services when no target is given, requires `--confirm` to write. | Guarded |
| `log-collect-diag` | Collect diagnostic data from a discovered LogService (LogService.CollectDiagnosticData); lists capable services when no target is given, requires `--confirm` to write. | Guarded |
| `manager` | Read manager data. | Read |
| `manager-network` | Read BMC ManagerNetworkProtocol service state, including HTTP/HTTPS/IPMI/SSH and NTP. | Read |
| `manager-reboot` | Reboot the BMC manager. | Write |
| `manager-time` | Read the BMC (Manager) clock; `--now`/`--set` write `DateTime` (read-only by default, no dry-run). | Write |
| `memory-metrics` | Read MemoryMetrics resources linked from Memory modules and Processor MemorySummary. | Read |
| `metric-definitions` | Read TelemetryService metric definitions. | Read |
| `metric-reports` | Read TelemetryService metric reports; `--report` filters by id substring. | Read |
| `network-adapters` | Read chassis NetworkAdapters such as NICs and DPUs. | Read |
| `network-ports` | Read NetworkAdapter port link state and speed. | Read |
| `ntp-set` | Set or clear Manager NTP servers through standard ManagerNetworkProtocol or legacy Manager NTP resources; dry-run by default and `--confirm` applies an NTP-only PATCH. | Guarded |
| `nvidia-debug-token` | List or invoke NVIDIA debug-token generate, disable, and install actions; previews by default and install token material is read from env/file. | Guarded |
| `nvlink-ports` | Read GPU NVLink port resources where the BMC exposes them. | Read |
| `oem-actions` | Read supported Dell OEM OS deployment actions. | Read |
| `oem-attach` | Attach a network ISO through a Dell OEM action. | Write |
| `oem-attach-status` | Read Dell OEM attach status. | Read |
| `oem-boot-netios` | Boot from a network ISO through a Dell OEM action. | Write |
| `oem-detach` | Detach Dell OEM network ISO media. | Write |
| `oem-disconnect` | Disconnect Dell OEM network ISO media. | Write |
| `oem-info` | Inventory vendor OEM extension blocks. | Read |
| `oem-net-ios-status` | Read Dell OEM network ISO status. | Read |
| `oem-net-iso-task` | Read Dell OEM OS deployment task data. | Read |
| `pci` | Read PCI device or function data. | Read |
| `power` | Read chassis PowerSubsystem, PowerSupplies, and Batteries resources. | Read |
| `power-smoothing` | Read NVIDIA GPU PowerSmoothing current state, preset profiles, and admin override profile setpoints. | Read |
| `power-smoothing-action` | Activate a NVIDIA GPU PowerSmoothing preset profile or apply admin overrides; dry-run by default and `--confirm` posts. | Guarded |
| `privilege-registry` | Read the privilege registry. | Read |
| `processor-metrics` | Read ProcessorMetrics resources linked from ComputerSystem processor members. | Read |
| `query` | Read an arbitrary Redfish resource path. | Read |
| `raid` | Read RAID service data. | Read |
| `reboot` | Reset the host ComputerSystem; `--dry_run` previews, but the command writes by default. | Write |
| `secure-boot` | Read SecureBoot state and key databases. | Read |
| `sensors` | Read Chassis Sensor collections across vendors (auto `$expand`, per-sensor fallback). | Read |
| `serial-console` | Report host serial redirection + BMC SOL; `--enable --confirm` sets both. | Guarded |
| `service-api-rs-status` | Read remote service API status. | Read |
| `service-api-status` | Read service API status. | Read |
| `spdm-measurements` | Fetch signed measurements from SPDM ComponentIntegrity resources. | Read |
| `storage-controllers` | Read storage controller information. | Read |
| `storage-convert-noraid` | Convert RAID disks under a controller to non-RAID. | Write |
| `smc-clear-policies` | Clear all Supermicro X10 Node Manager policies; previews unless `--confirm` is given. | Guarded |
| `storage-convert-raid` | Convert non-RAID disks under a controller to RAID. | Write |
| `storage-drives` | Read storage drive members. | Read |
| `storage-get` | Read one storage controller with optional `--filter Drives,Volumes`. | Read |
| `storage-list` | List storage devices. | Read |
| `subscription-create` | Create an EventDestination subscription; dry-run by default and `--confirm` POSTs. | Guarded |
| `subscription-delete` | Delete an EventDestination subscription by id or URI; dry-run by default and `--confirm` DELETEs. | Guarded |
| `system` | Read ComputerSystem data. | Read |
| `system-export` | Export system configuration. | Read |
| `system-import` | Import system configuration; may reboot depending on options. | Write |
| `system-reset` | Preview or perform a guarded ComputerSystem reset; requires `--confirm` to execute. | Guarded |
| `task-get` | Read one Redfish Task. | Read |
| `task-watch` | Watch task progress. | Read |
| `tasks` | Read the task collection. | Read |
| `telemetry-clear-reports` | Clear generated TelemetryService MetricReports; dry-run by default and `--confirm` posts. | Guarded |
| `telemetry-triggers` | Read TelemetryService triggers and thresholds. | Read |
| `thermal` | Read Chassis `ThermalSubsystem` links, ThermalMetrics temperature readings, and fan collection counts from `redfish_ctl/thermal/cmd_thermal.py`. | Read |
| `update-start` | Start updates staged for `UpdateService.StartUpdate`, the action advertised by UpdateService; previews unless `--confirm` is given. | Guarded |
| `update_service` | Read UpdateService inventory links, push URIs, and advertised actions. | Read |
| `vm-mount` | Mount/unmount an ISO via Supermicro OEM virtual media (CfgCD). | Write |
| `volume-check-consistency` | Start a Redfish Volume.CheckConsistency action; previews unless `--confirm` is given. | Guarded |
| `volume-create` | Create a Redfish volume; previews unless `--confirm` is given. | Guarded |
| `volume-delete` | Delete a Redfish volume; requires `--confirm` and `--confirm_volume_id`. | Guarded |
| `volume-get` | Read one volume from a storage device. | Read |
| `volume-init` | Initialize a volume. | Write |
| `volumes` | Read virtual disk data. | Read |
| `wait` | Wait for the BMC Redfish service to be reachable (e.g. after a reboot). | Read |
| `workload-power` | Enable or disable an NVIDIA WorkloadPower profile mask; previews by default and `--confirm` posts the action. | Guarded |

## Vendor-Neutral Telemetry Reads

```bash
redfish_ctl sensors
redfish_ctl metric-definitions
redfish_ctl metric-reports
redfish_ctl telemetry-triggers
redfish_ctl thermal
redfish_ctl power
redfish_ctl environment-metrics
redfish_ctl processor-metrics
redfish_ctl memory-metrics
redfish_ctl leak-detectors
redfish_ctl network-adapters
redfish_ctl network-ports
redfish_ctl ethernet-interfaces
redfish_ctl component-integrity
redfish_ctl secure-boot
redfish_ctl logs
redfish_ctl oem-info
```

These commands are the best starting point on non-Dell BMCs. They follow Redfish links and are
covered by the Dell, Supermicro, HPE, or generic fixture corpora listed in [Vendors](vendors.md).

## Mutating Workflow Pattern

Commands labeled **Guarded** block or preview writes by default and require an explicit intent flag,
usually `--confirm`. Commands labeled **Write** can mutate as soon as they are invoked; do not run
them live without explicit target approval, and use `--show` or `--dry_run` first when available.

Before running either kind of write, use the same four phases:

1. Read the current state.
2. Preview the change when the command supports `--show` or `--dry_run`.
3. Execute only on an approved target with the exact intended flags.
4. Verify with a read-only command or a job/task watch.

### BIOS Change From A Spec

```bash
redfish_ctl bios --filter ProcCStates,SysProfile,WorkloadProfile
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
```

After target approval, staging and reset commands stay separate from read/preview commands:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --commit
redfish_ctl bios-pending
redfish_ctl jobs
```

Many BIOS changes remain pending until an apply job and host reset. Use `-r` only during an approved
maintenance window:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
redfish_ctl jobs
```

Named profiles under `specs/profiles/` use the same staging path but add an automatic rollback
snapshot before the profile is previewed or staged:

```bash
redfish_ctl bios-profile list
redfish_ctl bios-profile show dell-cstates-off
redfish_ctl bios-profile apply dell-cstates-off
```

`bios-profile apply` is a dry-run by default. It reads the current BIOS values for the named
attributes, returns a rollback spec, and only stages the profile through `bios-change` when
`--confirm` is present:

```bash
redfish_ctl bios-profile apply dell-cstates-off --confirm
redfish_ctl bios-pending
```

### Secure Boot

```bash
redfish_ctl secure-boot
redfish_ctl bios-registry --attr_name SecureBoot
redfish_ctl bios-change --attr_name SecureBoot --attr_value Enabled on-reset --show
```

After approval for a host reset:

```bash
redfish_ctl bios-change --attr_name SecureBoot --attr_value Enabled on-reset -r
redfish_ctl secure-boot
```

### Virtual Media ISO Boot

```bash
redfish_ctl get_vm
redfish_ctl boot-one-shot --device Cd --dry_run
```

On older Supermicro X10 Redfish endpoints, `boot-one-shot` maps `Cd` to the
advertised `CD/DVD` target and uses that generation's top-level boot override
fields. Optional power-on or `-r` follow-up failures are returned as command
errors instead of being hidden.

After approval for live media and next-boot changes:

```bash
redfish_ctl eject_vm --device_id 1
redfish_ctl insert_vm --uri_path http://192.0.2.10/ubuntu.iso --device_id 1
redfish_ctl get_vm
redfish_ctl boot-one-shot --device Cd -r
redfish_ctl boot-state
```

### Power Reset

```bash
redfish_ctl system
redfish_ctl system-reset --reset_type GracefulRestart --dry_run
```

After approval for a live host reset:

```bash
redfish_ctl system-reset --reset_type GracefulRestart --confirm
redfish_ctl system
```

`system-reset` previews by default and performs the reset only when `--confirm` is present.
`reboot` also discovers the host `ComputerSystem.Reset` action, but it performs the reset by default
unless `--dry_run` is supplied; `--wait` only waits after a real reset.

### Firmware Update

```bash
redfish_ctl firmware_inventory
redfish_ctl firmware-update --image_uri https://example.invalid/firmware.exe --dry_run
redfish_ctl firmware-update --image_file ./firmware.bin --dry_run
```

After approval for a live firmware update:

```bash
redfish_ctl firmware-update --image_uri https://example.invalid/firmware.exe --confirm
redfish_ctl firmware-update --image_file ./firmware.bin --confirm
redfish_ctl tasks
```

`firmware-update`, defined in `redfish_ctl/firmware/cmd_firmware_update.py`, is destructive when
confirmed. It prefers the standard `SimpleUpdate` action when the BMC advertises it; otherwise it
uses `MultipartHttpPushUri`, then `HttpPushUri`, for local image uploads. Use only approved images
and approved non-production targets until you have your own firmware rollout process.

### HPE iLO Canary

`examples/hpe_ilo_canary.sh`, the live-emulator script under `examples/`, starts the HPE iLO emulator
and runs read-only vendor-neutral commands plus a dry-run `system-reset` preview:

```bash
bash examples/hpe_ilo_canary.sh
```
