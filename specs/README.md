# Specs

Author: Mus <spyroot@gmail.com>

These JSON files are examples for commands that read, stage, or apply Redfish settings. Treat them as
starting points: read the target registry first, preview with `--show` where the command supports it,
and apply only on an approved target.

Safety labels:

- **Read**: reads BMC state only.
- **Write**: stages or applies BMC or host state.

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
| `fastboot.spec.json` | `bios-change` | CPU/platform | Write | Faster POST BIOS attributes. |
| `realtime.opt.spec.json` | `bios-change` | CPU/platform | Write | Low-latency BIOS attributes. |
| `set_profile_example.json` | `bios-change` | Dell/iDRAC, CPU/platform | Write | Select a Dell `WorkloadProfile`. |
| `theramal_setting_and_boot_once_example.json` | `attr-update` | Dell/iDRAC, CPU/platform | Write | Thermal and first-boot attributes. |

For command flags, see [Command reference](../docs/commands.md). For the BIOS workflow around these
files, see [BIOS profiles](../docs/bios-profiles.md).
