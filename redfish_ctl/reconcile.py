"""Desired-state reconciliation primitives for service controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .api import RedfishApiError, SyncInvoker
from .idrac_shared import ApiRequestType


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
        """Build desired state from a CRD-style or JSON-style mapping."""
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
    """Plan desired state and apply only when explicitly confirmed."""
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
    return _mapping(_invoke_data(manager, api_call, name, **kwargs))


def _invoke_rows(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> tuple[Mapping[str, Any], ...]:
    return _mapping_rows(_invoke_data(manager, api_call, name, **kwargs))


def _invoke_data(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Any:
    result = manager.sync_invoke(api_call, name, **kwargs)
    if result.error:
        raise RedfishApiError(str(result.error))
    return result.data


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_tuple(value: Any) -> tuple[str, ...]:
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
    return ntp.get("ProtocolEnabled") is not None or bool(ntp.get("NTPServers"))


def _boot_already_matches(
    current: Mapping[str, Any],
    state: DesiredState,
) -> bool:
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
