# Desired-State Reconciler

`redfish_ctl.reconcile`, the library module added for service controllers, turns a desired node state
into a plan and only applies it when the caller passes `confirm=True`.

The `DesiredState` dataclass, defined in `redfish_ctl/reconcile.py`, currently accepts:

- `bios_profile`: a profile name from `specs/profiles/*.json`.
- `ntp_servers`: ManagerNetworkProtocol NTP servers for the guarded `ntp-set` command.
- `ntp_manager_id`: optional Manager id for the NTP change.
- `boot_device`, `boot_mode`, and `uefi_target`: one-time boot settings for `boot-one-shot`.
- `reset_type`: host reset type for the guarded reboot facade path.

`DesiredState.from_mapping()` accepts CRD-style keys such as `biosProfile`, `ntp.servers`,
`boot.device`, and `reboot.resetType`, so Kubernetes and proxy layers can pass structured specs
without depending on CLI argument names.

## Safety Model

`reconcile(manager, desired)` is a dry-run by default. In dry-run mode it may call read or preview
commands:

- `bios-profile diff`, from `redfish_ctl/bios/cmd_bios_profile.py`, reads current BIOS attributes.
- `ntp-set` without `confirm`, from `redfish_ctl/manager/cmd_ntp_set.py`, builds a PATCH plan but does
  not write it.
- `reboot` with `dry_run=True`, from `redfish_ctl/compute/cmd_power_state.py`, discovers the reset
  target and payload without POSTing.
- `boot-one-shot` is not invoked during dry-run because it has no preview-only command path.

`reconcile(manager, desired, confirm=True)` applies only the required planned changes:

- A BIOS profile is applied only when `bios-profile diff` reports `matches: false`.
- NTP settings are applied through `ntp-set --confirm`.
- A one-time boot target is applied through `boot-one-shot`.
- A reset is executed through the reboot facade path, with `wait_for_reboot=True` available to request
  the existing wait behavior.

The core does not create credentials, store secrets, or contact a BMC on its own. The caller supplies
the `SyncInvoker` manager, which is the same interface used by `redfish_ctl.api`.

## Example

```python
from redfish_ctl.reconcile import DesiredState, reconcile

desired = DesiredState.from_mapping({
    "biosProfile": "gb300-power-capped",
    "ntp": {"servers": ["0.pool.ntp.org"]},
    "boot": {"device": "Pxe", "mode": "UEFI"},
    "reboot": {"resetType": "GracefulRestart"},
})

plan = reconcile(manager, desired)
assert plan.dry_run is True

applied = reconcile(manager, desired, confirm=True, wait_for_reboot=True)
assert applied.dry_run is False
```
