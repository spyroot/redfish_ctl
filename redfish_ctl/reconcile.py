"""Desired-state reconciliation primitives for service controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .api import RedfishApiError, SyncInvoker
from .redfish_manager_shared import ApiRequestType


@dataclass(frozen=True)
class DesiredState:
    """Desired Redfish node state accepted by controller and service layers."""

    bios_profile: str | None = None
    ntp_servers: tuple[str, ...] = ()
    ntp_manager_id: str | None = None
    boot_device: str | None = None
    boot_mode: str | None = None
    uefi_target: str | None = None
    reset_type: str | None = None

    @classmethod
    def from_mapping(cls, spec: Mapping[str, Any]) -> "DesiredState":
        """Build desired state from a CRD-style or JSON-style mapping.

        :param spec: CRD-style or JSON-style mapping of the desired node state.
        :return: a :class:`DesiredState` built from ``spec``.
        """
        ntp = _mapping(spec.get("ntp"))
        boot = _mapping(spec.get("boot"))
        reboot_spec = spec.get("reboot")
        reboot = _mapping(reboot_spec)
        return cls(
            bios_profile=_optional_str(
                _first(spec, "biosProfile", "bios_profile")
            ),
            ntp_servers=_str_tuple(
                _first(ntp, "servers")
                or _first(spec, "ntpServers", "ntp_servers")
            ),
            ntp_manager_id=_optional_str(
                _first(ntp, "manager", "managerId", "manager_id")
            ),
            boot_device=_optional_str(_first(boot, "device")),
            boot_mode=_optional_str(_first(boot, "mode")),
            uefi_target=_optional_str(
                _first(boot, "uefiTarget", "uefi_target")
            ),
            reset_type=_reset_type(reboot_spec, reboot),
        )


@dataclass(frozen=True)
class ReconcileStep:
    """One planned reconciliation action."""

    kind: str
    required: bool
    description: str
    preview: Mapping[str, Any]


@dataclass(frozen=True)
class AppliedChange:
    """One confirmed reconciliation action result."""

    kind: str
    changed: bool
    result: Mapping[str, Any]


@dataclass(frozen=True)
class ReconcileResult:
    """Plan and optional apply result for one desired state."""

    dry_run: bool
    steps: tuple[ReconcileStep, ...]
    applied: tuple[AppliedChange, ...]


def reconcile(
    manager: SyncInvoker,
    desired: DesiredState | Mapping[str, Any],
    *,
    confirm: bool = False,
    wait_for_reboot: bool = False,
    async_call: bool = False,
) -> ReconcileResult:
    """Plan desired state and apply only when explicitly confirmed.

    :param manager: synchronous invoker used to run the underlying commands.
    :param desired: target state as a :class:`DesiredState` or a mapping.
    :param confirm: when False, only build the plan; when True, apply required steps.
    :param wait_for_reboot: wait for the host reset to finish (only when confirmed).
    :param async_call: issue the host reset as an asynchronous invocation.
    :return: a :class:`ReconcileResult` with the planned steps and applied changes.
    """
    state = (
        DesiredState.from_mapping(desired)
        if isinstance(desired, Mapping)
        else desired
    )
    steps: list[ReconcileStep] = []
    applied: list[AppliedChange] = []

    if state.bios_profile:
        _reconcile_bios_profile(manager, state, confirm, steps, applied)
    if state.ntp_servers:
        _reconcile_ntp(manager, state, confirm, steps, applied)
    if state.boot_device:
        _reconcile_boot(manager, state, confirm, steps, applied)
    if state.reset_type:
        _reconcile_reboot(
            manager,
            state,
            confirm,
            wait_for_reboot,
            async_call,
            steps,
            applied,
        )

    return ReconcileResult(
        dry_run=not confirm,
        steps=tuple(steps),
        applied=tuple(applied),
    )


def _reconcile_bios_profile(
    manager: SyncInvoker,
    state: DesiredState,
    confirm: bool,
    steps: list[ReconcileStep],
    applied: list[AppliedChange],
) -> None:
    """Plan and optionally apply the desired BIOS profile.

    :param manager: synchronous invoker used to run the bios-profile command.
    :param state: desired state carrying the target BIOS profile name.
    :param confirm: when True, apply the profile if the diff shows a change is required.
    :param steps: plan list the computed step is appended to.
    :param applied: results list an applied change is appended to.
    """
    profile_name = state.bios_profile
    diff = _invoke_mapping(
        manager,
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="diff",
        profile_name=profile_name,
    )
    required = not bool(diff.get("matches"))
    steps.append(
        ReconcileStep(
            kind="bios-profile",
            required=required,
            description=f"BIOS profile {profile_name}",
            preview=diff,
        )
    )
    if confirm and required:
        result = _invoke_mapping(
            manager,
            ApiRequestType.BiosProfile,
            "bios-profile",
            action="apply",
            profile_name=profile_name,
            confirm=True,
            dry_run=False,
        )
        applied.append(
            AppliedChange(
                kind="bios-profile",
                changed=True,
                result=result,
            )
        )


def _reconcile_ntp(
    manager: SyncInvoker,
    state: DesiredState,
    confirm: bool,
    steps: list[ReconcileStep],
    applied: list[AppliedChange],
) -> None:
    """Plan and optionally apply the desired Manager NTP servers.

    :param manager: synchronous invoker used to read and set NTP configuration.
    :param state: desired state carrying the NTP servers and optional manager id.
    :param confirm: when True, apply the NTP change through the guarded ntp-set command.
    :param steps: plan list the computed step is appended to.
    :param applied: results list an applied change is appended to.
    """
    current = _invoke_rows(
        manager,
        ApiRequestType.ManagerNetworkProtocol,
        "manager-network",
    )
    if _ntp_already_matches(current, state):
        steps.append(
            ReconcileStep(
                kind="ntp",
                required=False,
                description="Manager NTP servers",
                preview={
                    "servers": list(state.ntp_servers),
                    "manager": state.ntp_manager_id,
                    "current": list(current),
                },
            )
        )
        return

    result = _invoke_mapping(
        manager,
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=state.ntp_servers,
        manager_id=state.ntp_manager_id,
        confirm=confirm,
    )
    required = bool(result.get("plan") or result.get("applied"))
    steps.append(
        ReconcileStep(
            kind="ntp",
            required=required,
            description="Manager NTP servers",
            preview=result,
        )
    )
    if confirm:
        applied.append(
            AppliedChange(
                kind="ntp",
                changed=bool(result.get("applied")),
                result=result,
            )
        )


def _reconcile_boot(
    manager: SyncInvoker,
    state: DesiredState,
    confirm: bool,
    steps: list[ReconcileStep],
    applied: list[AppliedChange],
) -> None:
    """Plan and optionally apply the desired one-time boot override.

    :param manager: synchronous invoker used to read and set the boot override.
    :param state: desired state carrying the boot device, mode, and UEFI target.
    :param confirm: when True, apply the boot override if a change is required.
    :param steps: plan list the computed step is appended to.
    :param applied: results list an applied change is appended to.
    """
    current = _invoke_mapping(
        manager,
        ApiRequestType.CurrentBoot,
        "current_boot_query",
    )
    preview = {
        "device": state.boot_device,
        "mode": state.boot_mode,
        "uefiTarget": state.uefi_target,
        "current": current,
    }
    required = not _boot_already_matches(current, state)
    steps.append(
        ReconcileStep(
            kind="boot",
            required=required,
            description="One-time boot override",
            preview=preview,
        )
    )
    if confirm and required:
        result = _invoke_mapping(
            manager,
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            device=state.boot_device,
            mode=state.boot_mode,
            uefi_target=state.uefi_target,
            do_reboot=False,
        )
        applied.append(
            AppliedChange(kind="boot", changed=True, result=result)
        )


def _reconcile_reboot(
    manager: SyncInvoker,
    state: DesiredState,
    confirm: bool,
    wait_for_reboot: bool,
    async_call: bool,
    steps: list[ReconcileStep],
    applied: list[AppliedChange],
) -> None:
    """Plan and optionally apply the desired host reset.

    :param manager: synchronous invoker used to run the reboot command.
    :param state: desired state carrying the reset type.
    :param confirm: when True, execute the reset; otherwise plan it as a dry run.
    :param wait_for_reboot: wait for the reset to finish (only when confirmed).
    :param async_call: issue the reset as an asynchronous invocation.
    :param steps: plan list the computed step is appended to.
    :param applied: results list an applied change is appended to.
    """
    result = _invoke_mapping(
        manager,
        ApiRequestType.ComputerSystemReset,
        "reboot",
        reset_type=state.reset_type,
        dry_run=not confirm,
        do_wait=bool(wait_for_reboot and confirm),
        do_async=async_call,
    )
    steps.append(
        ReconcileStep(
            kind="reboot",
            required=True,
            description=f"Host reset {state.reset_type}",
            preview=result,
        )
    )
    if confirm:
        applied.append(
            AppliedChange(kind="reboot", changed=True, result=result)
        )


def _invoke_mapping(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Invoke a command and coerce its payload to a mapping.

    :param manager: synchronous invoker used to run the command.
    :param api_call: the :class:`ApiRequestType` to invoke.
    :param name: the registered command name to invoke.
    :return: the response payload as a mapping, or an empty mapping.
    """
    return _mapping(_invoke_data(manager, api_call, name, **kwargs))


def _invoke_rows(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> tuple[Mapping[str, Any], ...]:
    """Invoke a command and coerce its payload to a tuple of mapping rows.

    :param manager: synchronous invoker used to run the command.
    :param api_call: the :class:`ApiRequestType` to invoke.
    :param name: the registered command name to invoke.
    :return: the response rows as mappings; empty when the payload is not a list.
    """
    return _mapping_rows(_invoke_data(manager, api_call, name, **kwargs))


def _invoke_data(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Any:
    """Invoke a command and return its data, raising on a command error.

    :param manager: synchronous invoker used to run the command.
    :param api_call: the :class:`ApiRequestType` to invoke.
    :param name: the registered command name to invoke.
    :return: the command result data.
    :raises RedfishApiError: when the command returns an error.
    """
    result = manager.sync_invoke(api_call, name, **kwargs)
    if result.error:
        raise RedfishApiError(str(result.error))
    return result.data


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping.

    :param value: value to coerce.
    :return: ``value`` if it is a mapping, otherwise an empty dict.
    """
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return the mapping rows from a list or tuple value.

    :param value: value to coerce.
    :return: a tuple of the mapping items; empty when ``value`` is not a list or tuple.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Return the value of the first key present in ``mapping``.

    :param mapping: mapping to look keys up in.
    :return: the value of the first matching key, or None when none is present.
    """
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _optional_str(value: Any) -> str | None:
    """Coerce ``value`` to a stripped string, or None when empty.

    :param value: value to coerce.
    :return: the stripped string, or None when the value is None or blank.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce ``value`` to a tuple of non-empty strings.

    :param value: a comma-separated string or an iterable of values.
    :return: a tuple of stripped, non-empty strings; empty when ``value`` is None.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = value.split(",")
    else:
        values = value
    return tuple(
        item
        for raw in values
        if (item := str(raw).strip())
    )


def _ntp_already_matches(
    rows: tuple[Mapping[str, Any], ...],
    state: DesiredState,
) -> bool:
    """Return whether current NTP rows already satisfy the desired state.

    :param rows: current ManagerNetworkProtocol rows.
    :param state: desired state carrying the NTP servers and optional manager id.
    :return: True when a comparable row matches the desired servers with NTP enabled.
    """
    desired_servers = tuple(state.ntp_servers)
    comparable_rows = []
    for row in rows:
        manager_id = _optional_str(row.get("Manager"))
        if state.ntp_manager_id and manager_id != state.ntp_manager_id:
            continue
        ntp = _mapping(row.get("NTP"))
        if not _ntp_row_is_capable(ntp):
            if state.ntp_manager_id:
                return False
            continue
        comparable_rows.append(row)
        if ntp.get("ProtocolEnabled") is not True:
            return False
        if _str_tuple(ntp.get("NTPServers")) != desired_servers:
            return False
    return bool(comparable_rows)


def _ntp_row_is_capable(ntp: Mapping[str, Any]) -> bool:
    """Return whether an NTP row exposes usable NTP fields.

    :param ntp: the NTP sub-mapping of a ManagerNetworkProtocol row.
    :return: True when the row reports ProtocolEnabled or any NTPServers.
    """
    return ntp.get("ProtocolEnabled") is not None or bool(ntp.get("NTPServers"))


def _boot_already_matches(
    current: Mapping[str, Any],
    state: DesiredState,
) -> bool:
    """Return whether the current boot override already matches the desired state.

    :param current: current boot override fields from the ComputerSystem.
    :param state: desired state carrying the boot device, mode, and UEFI target.
    :return: True when the enabled state, target, mode, and UEFI target all match.
    """
    desired_device = state.boot_device
    desired_enabled = "Disabled" if desired_device == "None" else "Once"
    if current.get("BootSourceOverrideEnabled") != desired_enabled:
        return False
    if current.get("BootSourceOverrideTarget") != desired_device:
        return False
    if (
        state.boot_mode is not None
        and current.get("BootSourceOverrideMode") != state.boot_mode
    ):
        return False
    if (
        state.uefi_target is not None
        and current.get("UefiTargetBootSourceOverride") != state.uefi_target
    ):
        return False
    return True


def _reset_type(reboot_spec: Any, reboot: Mapping[str, Any]) -> str | None:
    """Resolve the reset type from a reboot spec.

    :param reboot_spec: the raw ``reboot`` spec value (string, bool, or mapping).
    :param reboot: the reboot spec coerced to a mapping.
    :return: the reset type string, or None when no reset is requested.
    :raises ValueError: when a truthy bare reboot or a non-string resetType is given.
    """
    if isinstance(reboot_spec, str):
        return _optional_str(reboot_spec)
    if isinstance(reboot_spec, bool):
        if reboot_spec:
            raise ValueError("reboot.resetType must be an explicit string")
        return None
    reset_type = _first(reboot, "resetType", "reset_type")
    if reset_type is None:
        return None
    if not isinstance(reset_type, str):
        raise ValueError("reboot.resetType must be an explicit string")
    return _optional_str(reset_type)
