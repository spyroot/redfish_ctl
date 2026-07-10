"""Typed helpers for embedding redfish_ctl in services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from .idrac_shared import ApiRequestType
from .redfish_manager import CommandResult


class SyncInvoker(Protocol):
    """Object that can invoke registered redfish_ctl commands synchronously."""

    def sync_invoke(
        self, api_call: ApiRequestType, name: str, **kwargs: Any
    ) -> CommandResult:
        ...


class RedfishApiError(RuntimeError):
    """Raised when a wrapped command returns a CommandResult error."""


@dataclass(frozen=True)
class SystemStatus:
    """ComputerSystem status fields most useful to service consumers."""

    id: str | None
    name: str | None
    power_state: str | None
    health: str | None
    state: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class SensorReading:
    """Normalized reading returned by the sensors command."""

    chassis: str | None
    name: str | None
    reading: int | float | str | None
    reading_units: str | None
    reading_type: str | None
    health: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class TemperatureReading:
    """ThermalSubsystem temperature row."""

    chassis: str | None
    device_name: str | None
    physical_context: str | None
    reading_celsius: int | float | str | None
    data_source_uri: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class FanReading:
    """ThermalSubsystem fan row."""

    chassis: str | None
    name: str | None
    state: str | None
    health: str | None
    speed_percent: int | float | str | None
    uri: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ThermalStatus:
    """Typed summary of the thermal command payload."""

    summary: Mapping[str, Any]
    temperatures: tuple[TemperatureReading, ...]
    fans: tuple[FanReading, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class GpuMetricRow:
    """Normalized GPU row from the gpu-metrics command."""

    system_id: str | None
    gpu_id: str | None
    processor_uri: str | None
    processor_metrics_uri: str | None
    name: str | None
    model: str | None
    manufacturer: str | None
    firmware_version: str | None
    status: Mapping[str, Any]
    operating_speed_mhz: int | float | str | None
    temperatures_celsius: Mapping[str, Any]
    compute_utilization_percent: Mapping[str, Any]
    throttle_duration_seconds: Mapping[str, Any]
    processor_metrics: Mapping[str, Any]
    memory: tuple[Mapping[str, Any], ...]
    memory_summary_metrics: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class GpuMetricsStatus:
    """Typed summary of the gpu-metrics command payload."""

    summary: Mapping[str, Any]
    gpus: tuple[GpuMetricRow, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NtpTarget:
    """A ManagerNetworkProtocol resource that can receive an NTP PATCH."""

    manager: str | None
    target: str | None
    payload: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NtpSkipped:
    """A ManagerNetworkProtocol resource skipped by the ntp-set command."""

    manager: str | None
    target: str | None
    reason: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NtpApplied:
    """A ManagerNetworkProtocol PATCH result from the ntp-set command."""

    manager: str | None
    target: str | None
    status: str | None
    error: Any
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NtpSetResult:
    """Typed result returned by the guarded ntp-set command."""

    dry_run: bool
    servers: tuple[str, ...]
    plan: tuple[NtpTarget, ...]
    skipped: tuple[NtpSkipped, ...]
    applied: tuple[NtpApplied, ...]
    note: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class RebootResult:
    """Typed host reset result from the reboot command."""

    reset_type: str | None
    dry_run: bool
    target: str | None
    payload: Mapping[str, Any]
    task_id: str | None
    task_state: Any
    raw: Mapping[str, Any]


def _invoke(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Any:
    result = manager.sync_invoke(api_call, name, **kwargs)
    if result.error:
        raise RedfishApiError(str(result.error))
    return result.data


def _mapping(data: Any) -> Mapping[str, Any]:
    return data if isinstance(data, Mapping) else {}


def _rows(data: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(data, list):
        return ()
    return tuple(row for row in data if isinstance(row, Mapping))


def _server_list(servers: str | Iterable[str]) -> list[str]:
    if isinstance(servers, str):
        return [servers]
    return list(servers)


def _server_tuple(data: Any, fallback: Iterable[str]) -> tuple[str, ...]:
    values = data if isinstance(data, list) else list(fallback)
    return tuple(str(value) for value in values)


def get_system(manager: SyncInvoker, *, deep: bool = False) -> SystemStatus:
    """Return typed ComputerSystem status through the existing system command."""
    data = _mapping(
        _invoke(
            manager,
            ApiRequestType.SystemQuery,
            "system_query",
            do_deep=deep,
        )
    )
    status = _mapping(data.get("Status"))
    return SystemStatus(
        id=data.get("Id"),
        name=data.get("Name"),
        power_state=data.get("PowerState"),
        health=status.get("Health"),
        state=status.get("State"),
        raw=data,
    )


def get_sensors(
    manager: SyncInvoker, *, expanded: bool = False
) -> tuple[SensorReading, ...]:
    """Return typed chassis sensor readings through the sensors command."""
    rows = _rows(
        _invoke(
            manager,
            ApiRequestType.Sensors,
            "sensors",
            do_expanded=expanded,
        )
    )
    return tuple(
        SensorReading(
            chassis=row.get("Chassis"),
            name=row.get("Name"),
            reading=row.get("Reading"),
            reading_units=row.get("ReadingUnits"),
            reading_type=row.get("ReadingType"),
            health=row.get("Health"),
            raw=row,
        )
        for row in rows
    )


def get_thermal(manager: SyncInvoker) -> ThermalStatus:
    """Return typed thermal status through the thermal command."""
    data = _mapping(_invoke(manager, ApiRequestType.Thermal, "thermal"))
    temperature_rows = _rows(data.get("temperature_readings"))
    fan_rows = _rows(data.get("fans"))
    temperatures = tuple(
        TemperatureReading(
            chassis=row.get("Chassis"),
            device_name=row.get("DeviceName"),
            physical_context=row.get("PhysicalContext"),
            reading_celsius=row.get("ReadingCelsius"),
            data_source_uri=row.get("DataSourceUri"),
            raw=row,
        )
        for row in temperature_rows
    )
    fans = tuple(
        FanReading(
            chassis=row.get("Chassis"),
            name=row.get("Name"),
            state=row.get("State"),
            health=row.get("Health"),
            speed_percent=row.get("SpeedPercent"),
            uri=row.get("Uri"),
            raw=row,
        )
        for row in fan_rows
    )
    return ThermalStatus(
        summary=_mapping(data.get("summary")),
        temperatures=temperatures,
        fans=fans,
        raw=data,
    )


def get_gpu_metrics(manager: SyncInvoker) -> GpuMetricsStatus:
    """Return typed GPU metric rows through the gpu-metrics command."""
    data = _mapping(_invoke(manager, ApiRequestType.GpuMetrics, "gpu-metrics"))
    gpu_rows = tuple(
        GpuMetricRow(
            system_id=row.get("SystemId"),
            gpu_id=row.get("GpuId"),
            processor_uri=row.get("ProcessorUri"),
            processor_metrics_uri=row.get("ProcessorMetricsUri"),
            name=row.get("Name"),
            model=row.get("Model"),
            manufacturer=row.get("Manufacturer"),
            firmware_version=row.get("FirmwareVersion"),
            status=_mapping(row.get("Status")),
            operating_speed_mhz=row.get("OperatingSpeedMHz"),
            temperatures_celsius=_mapping(row.get("TemperaturesCelsius")),
            compute_utilization_percent=_mapping(
                row.get("ComputeUtilizationPercent")
            ),
            throttle_duration_seconds=_mapping(
                row.get("ThrottleDurationSeconds")
            ),
            processor_metrics=_mapping(row.get("ProcessorMetrics")),
            memory=_rows(row.get("Memory")),
            memory_summary_metrics=_mapping(row.get("MemorySummaryMetrics")),
            raw=row,
        )
        for row in _rows(data.get("gpus"))
    )
    return GpuMetricsStatus(
        summary=_mapping(data.get("summary")),
        gpus=gpu_rows,
        raw=data,
    )


def set_ntp(
    manager: SyncInvoker,
    servers: str | Iterable[str],
    *,
    manager_id: str | None = None,
    confirm: bool = False,
) -> NtpSetResult:
    """Preview or apply NTP servers through the guarded ntp-set command."""
    requested_servers = _server_list(servers)
    data = _mapping(
        _invoke(
            manager,
            ApiRequestType.NtpSet,
            "ntp-set",
            servers=requested_servers,
            manager_id=manager_id,
            confirm=confirm,
        )
    )
    plan = tuple(
        NtpTarget(
            manager=row.get("Manager"),
            target=row.get("target"),
            payload=_mapping(row.get("payload")),
            raw=row,
        )
        for row in _rows(data.get("plan"))
    )
    skipped = tuple(
        NtpSkipped(
            manager=row.get("Manager"),
            target=row.get("target"),
            reason=row.get("reason"),
            raw=row,
        )
        for row in _rows(data.get("skipped"))
    )
    applied = tuple(
        NtpApplied(
            manager=row.get("Manager"),
            target=row.get("target"),
            status=row.get("status"),
            error=row.get("error"),
            raw=row,
        )
        for row in _rows(data.get("applied"))
    )
    note = data.get("note")
    return NtpSetResult(
        dry_run=bool(data.get("dry_run", False)),
        servers=_server_tuple(data.get("servers"), requested_servers),
        plan=plan,
        skipped=skipped,
        applied=applied,
        note=note if isinstance(note, str) else None,
        raw=data,
    )


def reboot(
    manager: SyncInvoker,
    *,
    reset_type: str = "GracefulRestart",
    confirm: bool = False,
    wait: bool = False,
    async_call: bool = False,
) -> RebootResult:
    """Preview or execute a host ComputerSystem reset through reboot."""
    data = _mapping(
        _invoke(
            manager,
            ApiRequestType.ComputerSystemReset,
            "reboot",
            reset_type=reset_type,
            dry_run=not confirm,
            do_wait=bool(wait and confirm),
            do_async=async_call,
        )
    )
    payload = _mapping(data.get("payload"))
    return RebootResult(
        reset_type=payload.get("ResetType") or reset_type,
        dry_run=bool(data.get("dry_run", not confirm)),
        target=data.get("target"),
        payload=payload,
        task_id=data.get("task_id"),
        task_state=data.get("task_state"),
        raw=data,
    )
