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
    result = _invoke_mapping(
        manager,
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=state.ntp_servers,
        manager_id=state.ntp_manager_id,
        confirm=confirm,
    )
    required = confirm or bool(result.get("plan") or result.get("applied"))
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
    preview = {
        "device": state.boot_device,
        "mode": state.boot_mode,
        "uefiTarget": state.uefi_target,
    }
    steps.append(
        ReconcileStep(
            kind="boot",
            required=True,
            description="One-time boot override",
            preview=preview,
        )
    )
    if confirm:
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
    result = manager.sync_invoke(api_call, name, **kwargs)
    if result.error:
        raise RedfishApiError(str(result.error))
    return _mapping(result.data)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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


def _reset_type(reboot_spec: Any, reboot: Mapping[str, Any]) -> str | None:
    if isinstance(reboot_spec, str):
        return _optional_str(reboot_spec)
    if reboot_spec is True:
        return "GracefulRestart"
    return _optional_str(_first(reboot, "resetType", "reset_type"))
