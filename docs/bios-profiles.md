# BIOS Profiles

Author: Mus <spyroot@gmail.com>

BIOS profiles are how I make repeatable host tuning safe enough to review. The pattern is always:
inspect the registry, preview the change, stage it, then verify after the reset.

## The Safe Pattern

```bash
redfish_ctl bios-registry --attr_name SysProfile
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --commit
redfish_ctl jobs
redfish_ctl bios --filter SysProfile,ProcCStates,MemFrequency
```

Use `-r` only when you are ready for the host reset:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
```

`bios-change`, defined in `redfish_ctl/bios/cmd_change_bios.py`, requires an apply mode:
`on-reset`, `auto-boot`, or `maintenance`. `--show` previews the payload and does not apply changes.

## Included Examples

| Example | What it does |
|---|---|
| `examples/example_low_latency_profile.sh` | Applies `specs/realtime.opt.spec.json` for lower jitter. |
| `examples/example_dell_system_profile.sh` | Uses Dell `SysProfile` or newer `WorkloadProfile` presets. |
| `examples/example_custom_profile.sh` | Builds a small JSON spec and applies it as a custom profile. |
| `examples/example_bios_optimize_intel.sh` | Shows Intel Xeon performance/power knobs after registry lookup. |
| `examples/example_bios_optimize_amd.sh` | Shows AMD EPYC NUMA/performance knobs after registry lookup. |
| `examples/example_fast_boot.sh` | Disables long boot-time checks where the BIOS supports it. |

## Low Latency

The low-latency profile turns off common jitter sources such as deep CPU C-states and long memory
tests, then enables high-performance memory and SR-IOV knobs where the platform supports them.

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
```

Always verify attribute names and allowed values on the target BMC. Dell, HPE, and other vendors do
not use exactly the same BIOS registry names.

## Dell System Profile

Dell PowerEdge systems often expose one high-level `SysProfile` attribute:

```bash
redfish_ctl bios-registry --attr_name SysProfile
redfish_ctl bios-change --attr_name SysProfile --attr_value PerfOptimized on-reset --show
redfish_ctl bios-change --attr_name SysProfile --attr_value PerfOptimized on-reset -r
```

Newer systems can also expose `WorkloadProfile`; read the registry before assuming the value name.

## Custom Profile

A custom profile is just a JSON spec with an `Attributes` object:

```json
{
  "Attributes": {
    "SysProfile": "Custom",
    "ProcCStates": "Disabled",
    "ProcTurboMode": "Enabled"
  }
}
```

Save it, preview it, then apply it:

```bash
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset --show
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset -r
```

## Intel And AMD Notes

The Intel and AMD scripts are intentionally registry-first. They show the class of knobs I care about
for performance work, but the exact attribute names depend on platform generation and BIOS version.
If `bios-registry --attr_name <name>` does not show the attribute, do not apply that line blindly.
