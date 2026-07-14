# Examples

Author: Mus <spyroot@gmail.com>

These scripts are small operator recipes. Read them before running them: many examples stage BIOS,
boot, storage, virtual-media, or firmware-adjacent changes on real hardware.

Set the normal connection variables first:

```bash
export REDFISH_IP=10.0.0.42
export REDFISH_USERNAME=root
export REDFISH_PASSWORD='your-password'
export REDFISH_PORT=443
```

Safety labels:

- **Read**: reads BMC state only.
- **Guarded**: previews or depends on a guarded command.
- **Write**: changes BMC or host state when the script reaches that step.

Platform/vendor labels:

- **Dell/iDRAC**: uses Dell-shaped ids, Dell OEM actions, or iDRAC manager attributes.
- **Supermicro/GB300**: uses Supermicro OEM Redfish paths or GB300-era provisioning patterns.
- **HPE iLO**: uses HPE iLO OEM paths or the HPE iLO emulator.
- **generic/vendor-neutral**: uses standard Redfish commands or vendor-neutral `redfish_ctl` paths.
- **CPU/platform**: BIOS tuning depends on processor, platform generation, or firmware registry names.

## Index

Run examples from the repository root with `bash examples/<script>`.

| Example | Platform/vendor | Safety | Purpose |
|---|---|---|---|
| `exameple_volume_setup.sh` | Dell/iDRAC | Write | Initialize one volume by device and volume id. |
| `example_account_lifecycle.sh` | generic/vendor-neutral | Write | Create, inspect, update, and delete a Redfish account. |
| `example_account_sshkey.sh` | HPE iLO | Write | Authorize or clear an account SSH public key through HPE OEM data. |
| `example_adjust_profile.sh` | Dell/iDRAC, CPU/platform | Write | Build, preview, and apply a `WorkloadProfile` BIOS spec. |
| `example_bios_optimize_amd.sh` | CPU/platform | Write | Show AMD EPYC NUMA/performance BIOS knobs after registry lookup. |
| `example_bios_optimize_intel.sh` | CPU/platform | Write | Show Intel Xeon performance/power BIOS knobs after registry lookup. |
| `example_bios_transaction.sh` | generic/vendor-neutral | Write | Capture a BIOS restore point, apply a change, and roll back if needed. |
| `example_bios_tuning.sh` | CPU/platform | Write | Change one BIOS attribute and verify it after reboot. |
| `example_bmc_scan.sh` | generic/vendor-neutral | Read | Find Redfish BMCs on a network segment before provisioning. |
| `example_boot_from_iso_one_shot.sh` | generic/vendor-neutral | Write | Mount an ISO as virtual media and set one-time CD boot. |
| `example_boot_state.sh` | generic/vendor-neutral | Read | Infer boot mode, next target, boot order, and staged media. |
| `example_change_bios.sh` | CPU/platform | Write | Preview and apply a two-attribute BIOS change. |
| `example_console_access.sh` | generic/vendor-neutral | Read | Show Redfish console metadata and out-of-band console options. |
| `example_convert_noraid.sh` | Dell/iDRAC | Write | Convert RAID-capable disks under one controller to non-RAID. |
| `example_custom_profile.sh` | CPU/platform | Write | Build a custom BIOS spec, preview it, and apply it. |
| `example_dell_network_cif_ios.sh` | Dell/iDRAC | Write | Boot a Dell host from a CIFS-backed network ISO. |
| `example_dell_system_profile.sh` | Dell/iDRAC, CPU/platform | Write | Apply a Dell `SysProfile` or newer `WorkloadProfile`. |
| `example_discover_host.sh` | generic/vendor-neutral | Read | Run inventory and discovery reads on an unknown host. |
| `example_enable_secure_boot.sh` | generic/vendor-neutral, CPU/platform | Write | Stage Secure Boot and verify databases. |
| `example_export_import.sh` | Dell/iDRAC | Write | Export system config, import an edited config, and verify a BIOS field. |
| `example_fast_boot.sh` | CPU/platform | Write | Enable faster boot-related BIOS settings where supported. |
| `example_inventory_walk.sh` | generic/vendor-neutral | Read | Read system, chassis, PCI, storage, drives, and boot state. |
| `example_jobs.sh` | Dell/iDRAC | Write | Read jobs, watch one job, then delete one approved job. |
| `example_low_latency_profile.sh` | CPU/platform | Write | Apply the low-latency BIOS profile from `specs/realtime.opt.spec.json`. |
| `example_manager_time.sh` | generic/vendor-neutral | Write | Read or set the BMC manager clock before trusting log timestamps. |
| `example_provision_boot_iso.sh` | generic/vendor-neutral | Write | Mount an installer ISO and boot from it once. |
| `example_sensors_read.sh` | generic/vendor-neutral | Read | Read and filter temperatures, fans, power supplies, and voltages. |
| `example_supermicro_provision_from_iso.sh` | Supermicro/GB300 | Write | Mount Supermicro virtual media and boot once. |
| `example_wait.sh` | generic/vendor-neutral | Read | Wait for Redfish service recovery after an approved BMC restart. |
| `hpe_ilo_canary.sh` | HPE iLO | Guarded | Run read-only commands and a dry-run reset against the iLO emulator. |

For BIOS profile flow, see [BIOS profiles](../docs/bios-profiles.md). For every command name, see
[Command reference](../docs/commands.md). JSON specs are indexed in [Specs](../specs/README.md).
