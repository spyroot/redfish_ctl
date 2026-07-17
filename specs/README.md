# Specs

Author: Mus <spyroot@gmail.com>

These JSON files are examples for commands that read, stage, or apply Redfish settings. Treat them as
starting points: read the target registry first, preview with `--show` where the command supports it,
and apply only on an approved target.

Safety labels:

- **Read**: reads BMC state only.
- **Guarded**: previews by default or requires an explicit apply flag such as `--confirm`.
- **Write**: can stage or apply BMC or host state; do not run live without explicit target approval.

Platform/vendor labels:

- **Dell/iDRAC**: uses Dell-shaped ids, iDRAC manager attributes, or Dell boot naming.
- **Supermicro/GB300**: uses Supermicro OEM Redfish paths or GB300-era platform fields.
- **HPE iLO**: uses HPE iLO OEM paths or HPE-specific Redfish fields.
- **generic/vendor-neutral**: uses standard Redfish shape or a portable query list.
- **CPU/platform**: BIOS tuning depends on processor, platform generation, or firmware registry names.

Use these files with the command's `--from_spec` option unless the table says otherwise.

| Spec | Command | Platform/vendor | Safety | Purpose |
|---|---|---|---|---|
| `add_read_only_user.json` | `attr-update` | Dell/iDRAC | Write | Sample user attributes; clean demo fields before use. |
| `attribute_example.json` | `attr-update` | Dell/iDRAC | Write | Set `OwnerInfo.1.OwnerName`. |
| `attribute_example02.json` | `attr-update` | Dell/iDRAC | Write | Set an acquisition/cost-center attribute. |
| `bios_query.json` | `bios --filter` | generic/vendor-neutral, CPU/platform | Read | Reusable BIOS attribute filter list. |
| `change_boot_order_spec01.json` | `change-boot-order` | Dell/iDRAC | Write | Disk/NIC-first boot order. |
| `change_boot_order_spec02.json` | `change-boot-order` | Dell/iDRAC | Write | Move virtual media earlier. |
| `change_boot_order_spec03.json` | `change-boot-order` | Dell/iDRAC | Write | Put NIC entries first. |
| `change_boot_source_spec01.json` | `boot-source-update` | Dell/iDRAC | Write | Enable/disable Dell boot entries. |
| `exporter_signalfx_spec.json` | `exporter --exporter-config` | generic/vendor-neutral | Read | Sample SignalFx ingest URL, token-file path, and identity label math. |
| `fastboot.spec.json` | `bios-change` | CPU/platform | Write | Faster POST BIOS attributes. |
| `realtime.opt.spec.json` | `bios-change` | CPU/platform | Write | Low-latency BIOS attributes. |
| `set_profile_example.json` | `bios-change` | Dell/iDRAC, CPU/platform | Write | Select a Dell `WorkloadProfile`. |
| `theramal_setting_and_boot_once_example.json` | `attr-update` | Dell/iDRAC, CPU/platform | Write | Thermal and first-boot attributes. |

## Named BIOS Profiles

`specs/profiles/`, the committed named-profile directory, defines profiles for the `bios-profile`
command. `bios-profile list` and `bios-profile show` are local reads; `bios-profile apply` is guarded
and stages only with `--confirm`.

| Spec | Command | Platform/vendor | Safety | Purpose |
|---|---|---|---|---|
| `profiles/dell-cstates-off.json` | `bios-profile apply` | Dell/iDRAC, CPU/platform | Guarded | Disable processor C-states for latency-sensitive Dell PowerEdge workloads. |
| `profiles/gb300-extended-gpu-memory.json` | `bios-profile apply` | Supermicro/GB300 | Guarded | Enable Extended GPU Memory on Grace-Blackwell nodes. |
| `profiles/gb300-power-capped.json` | `bios-profile apply` | Supermicro/GB300 | Guarded | Use 1 s input-power capping for smoother rack-level power draw. |

For command flags, see [Command reference](../docs/commands.md). For the BIOS workflow around these
files, see [BIOS profiles](../docs/bios-profiles.md).
