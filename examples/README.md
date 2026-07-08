# Examples

Author: Mus <spyroot@gmail.com>

These scripts are small operator recipes. Read them before running them: many examples stage BIOS,
boot, storage, virtual-media, or firmware-adjacent changes on real hardware.

Set the normal connection variables first:

```bash
export IDRAC_IP=10.0.0.42
export IDRAC_USERNAME=root
export IDRAC_PASSWORD='your-password'
export IDRAC_PORT=443
```

Safety labels:

- **Read**: reads BMC state only.
- **Guarded**: previews or depends on a guarded command.
- **Write**: changes BMC or host state.

## Index

- `exameple_volume_setup.sh` (**Write**) initializes one volume by device and volume id.
  Run: `bash examples/exameple_volume_setup.sh`
- `example_adjust_profile.sh` (**Write**) builds a WorkloadProfile spec, previews it, then applies it
  on reset.
  Run: `bash examples/example_adjust_profile.sh`
- `example_bios_optimize_amd.sh` (**Write**) shows AMD EPYC NUMA/performance BIOS knobs after
  registry lookup.
  Run: `bash examples/example_bios_optimize_amd.sh`
- `example_bios_optimize_intel.sh` (**Write**) shows Intel Xeon performance/power BIOS knobs after
  registry lookup.
  Run: `bash examples/example_bios_optimize_intel.sh`
- `example_bios_tuning.sh` (**Write**) changes one BIOS attribute and verifies it after reboot.
  Run: `bash examples/example_bios_tuning.sh`
- `example_boot_from_iso_one_shot.sh` (**Write**) mounts an ISO as virtual media and sets one-time
  CD boot.
  Run: `bash examples/example_boot_from_iso_one_shot.sh`
- `example_change_bios.sh` (**Write**) previews and applies a two-attribute BIOS change.
  Run: `bash examples/example_change_bios.sh`
- `example_console_access.sh` (**Read**) shows Redfish console access metadata and how to reach the
  console out of band.
  Run: `bash examples/example_console_access.sh`
- `example_convert_noraid.sh` (**Write**) converts RAID-capable disks under one controller to
  non-RAID.
  Run: `bash examples/example_convert_noraid.sh`
- `example_custom_profile.sh` (**Write**) builds a custom BIOS spec, previews it, then applies it.
  Run: `bash examples/example_custom_profile.sh`
- `example_dell_network_cif_ios.sh` (**Write**) boots a Dell host from a CIFS-backed network ISO.
  Run: `bash examples/example_dell_network_cif_ios.sh`
- `example_dell_system_profile.sh` (**Write**) applies a Dell `SysProfile` or newer
  `WorkloadProfile`.
  Run: `bash examples/example_dell_system_profile.sh`
- `example_discover_host.sh` (**Read**) runs a quick inventory/discovery pass on an unknown host.
  Run: `bash examples/example_discover_host.sh`
- `example_enable_secure_boot.sh` (**Write**) reads Secure Boot state, enables the BIOS attribute,
  then verifies databases.
  Run: `bash examples/example_enable_secure_boot.sh`
- `example_export_import.sh` (**Write**) exports system config, imports an edited config, and
  verifies a BIOS field.
  Run: `bash examples/example_export_import.sh`
- `example_fast_boot.sh` (**Write**) enables faster boot-related BIOS settings where supported.
  Run: `bash examples/example_fast_boot.sh`
- `example_inventory_walk.sh` (**Read**) reads system, chassis, PCI, storage, drives, and current
  boot state.
  Run: `bash examples/example_inventory_walk.sh`
- `example_jobs.sh` (**Write**) reads jobs, watches one job, then deletes one approved job.
  Run: `bash examples/example_jobs.sh`
- `example_low_latency_profile.sh` (**Write**) applies the low-latency BIOS profile in
  `specs/realtime.opt.spec.json`.
  Run: `bash examples/example_low_latency_profile.sh`
- `example_provision_boot_iso.sh` (**Write**) mounts an installer ISO and boots from it once.
  Run: `bash examples/example_provision_boot_iso.sh`
- `example_sensors_read.sh` (**Read**) reads sensors and filters temperatures, fans, power supplies,
  and voltages with `jq`.
  Run: `bash examples/example_sensors_read.sh`
- `example_supermicro_provision_from_iso.sh` (**Write**) mounts an OS installer ISO on a Supermicro
  BMC over SMB (SMB1 for old X10-era BMCs) and boots it once in UEFI (`UefiUsbCd`) to run an
  unattended install.
  Run: `bash examples/example_supermicro_provision_from_iso.sh`
- `hpe_ilo_canary.sh` (**Guarded**) starts the HPE iLO emulator and runs read-only commands plus a
  dry-run reset preview.
  Run: `bash examples/hpe_ilo_canary.sh`

For BIOS profile flow, see [BIOS profiles](../docs/bios-profiles.md). For every command name, see
[Command reference](../docs/commands.md).
