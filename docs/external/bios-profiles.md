# BIOS Profiles

Author: Mus <spyroot@gmail.com>

BIOS profiles make host tuning repeatable and reviewable: each profile is a named set of BIOS
attributes that can be inspected, previewed, staged, and verified as one unit instead of as ad-hoc
edits. The working pattern is always the same: inspect the registry, preview the change, stage it
only after approval, then verify after the approved maintenance reset.

The spec files used below are indexed with vendor and safety labels in [Specs](../../specs/README.md).

## Read, Preview, Stage, Verify

```bash
redfish_ctl bios-registry --attr_name SysProfile
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
```

After approval, stage the pending BIOS change and verify the pending state:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --commit
redfish_ctl bios-pending
redfish_ctl jobs
```

Use `-r` only during an approved maintenance window, then verify after the host returns:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
redfish_ctl jobs
redfish_ctl bios --filter SysProfile,ProcCStates,MemFrequency
```

`bios-change`, defined in `redfish_ctl/bios/cmd_change_bios.py`, requires an apply mode:
`on-reset`, `auto-boot`, or `maintenance`. `--show` previews the payload and does not apply changes.

## Named Profile Catalog

`specs/profiles/`, the committed named-profile directory, holds vendor-scoped JSON profiles with the
schema described in [BIOS tuning profiles](../../specs/profiles/README.md). Each profile lists the BIOS
attributes to stage and must match a captured vendor registry before it is accepted by the test suite.

`bios-profile`, the CLI's named-profile command, reads that directory for `list` and `show` without
contacting a BMC:

```bash
redfish_ctl bios-profile list
redfish_ctl bios-profile show gb300-power-capped
```

Use the catalog by operator purpose first, then inspect the exact attributes before staging anything:

| Profile | Platform/vendor | Safety | Verify with | Key attribute |
|---|---|---|---|---|
| `gb300-power-capped` | Supermicro/GB300 | Guarded | `bios-pending`, `bios --filter ServerPowerControl` | `ServerPowerControl=InputPowerCappingUsing1sTimescale` |
| `gb300-extended-gpu-memory` | Supermicro/GB300 | Guarded | `bios-pending`, `bios --filter EGM` | `EGM=true` |
| `dell-cstates-off` | Dell/iDRAC, CPU/platform | Guarded | `bios-pending`, `bios --filter ProcCStates` | `ProcCStates=Disabled` |

The intended named-profile workflow is read, compare, then stage:

```bash
redfish_ctl bios-profile list
redfish_ctl bios-profile show gb300-power-capped
redfish_ctl bios-profile diff gb300-power-capped
redfish_ctl bios-profile apply gb300-power-capped --dry_run
```

The catalog command ships all four actions: `list` and `show` read only the local catalog, `diff`
compares a profile against the current BIOS attributes over a read-only BMC query, and guarded
`apply` previews by default and stages only with `--confirm`, capturing a rollback snapshot first.
Applying a profile stages BIOS settings and can require a host reset; only run the apply step during
an approved maintenance window.

```bash
redfish_ctl bios-profile apply gb300-power-capped --confirm
redfish_ctl bios-pending
```

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
```

After approval:

```bash
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
```

Always verify attribute names and allowed values on the target BMC. Dell, HPE, and other vendors do
not use exactly the same BIOS registry names.

## Dell System Profile

Dell PowerEdge systems often expose one high-level `SysProfile` attribute:

```bash
redfish_ctl bios-registry --attr_name SysProfile
redfish_ctl bios-change --attr_name SysProfile --attr_value PerfOptimized on-reset --show
```

After approval:

```bash
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

Save it and preview it:

```bash
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset --show
```

After approval:

```bash
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset -r
```

## Intel And AMD Notes

The Intel and AMD scripts are intentionally registry-first. They show the class of knobs that matter
for performance work, but the exact attribute names depend on platform generation and BIOS version.
If `bios-registry --attr_name <name>` does not show the attribute, do not apply that line blindly.
