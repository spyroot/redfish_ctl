"""Typed read helpers for embedding redfish_ctl in services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

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
