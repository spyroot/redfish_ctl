"""Dependency-light read proxy core for Redfish fleet services."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redfish_ctl.api import (
    RedfishApiError,
    SyncInvoker,
    ThermalStatus,
    get_sensors,
    get_system,
    get_thermal,
)
from redfish_ctl.idrac_shared import ApiRequestType


@dataclass(frozen=True)
class NodeConfig:
    """Connection metadata for one managed BMC."""

    id: str
    address: str
    port: int = 443
    username: str | None = None
    password: str | None = None
    insecure: bool = True
    description: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return node metadata safe for API responses."""
        return {
            "id": self.id,
            "address": self.address,
            "port": self.port,
            "insecure": self.insecure,
            "description": self.description,
        }


class NodeNotFound(KeyError):
    """Raised when a proxy request references an unknown node."""


class NodeRegistry:
    """In-memory node registry for the first read-only proxy increment."""

    def __init__(self, nodes: Iterable[NodeConfig]):
        self._nodes = {}
        for node in nodes:
            if node.id in self._nodes:
                raise ValueError(f"duplicate node id: {node.id}")
            self._nodes[node.id] = node

    def list(self) -> list[NodeConfig]:
        """Return nodes sorted by stable id."""
        return [self._nodes[node_id] for node_id in sorted(self._nodes)]

    def get(self, node_id: str) -> NodeConfig:
        """Return one node or raise NodeNotFound."""
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise NodeNotFound(node_id) from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _temperature_summary(thermal: ThermalStatus) -> dict[str, float | int | None]:
    values = [
        numeric
        for reading in thermal.temperatures
        if (numeric := _as_float(reading.reading_celsius)) is not None
    ]
    return {
        "count": len(values),
        "maxCelsius": max(values) if values else None,
    }


def _raw_command(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Any:
    result = manager.sync_invoke(api_call, name, **kwargs)
    if result.error:
        raise RedfishApiError(str(result.error))
    return result.data


def _sensor_dicts(manager: SyncInvoker) -> list[dict[str, Any]]:
    return [
        {
            "chassis": reading.chassis,
            "name": reading.name,
            "reading": reading.reading,
            "readingUnits": reading.reading_units,
            "readingType": reading.reading_type,
            "health": reading.health,
        }
        for reading in get_sensors(manager)
    ]


class ReadOnlyProxy:
    """Read-only facade that shapes Redfish command results for service APIs."""

    def __init__(
        self,
        registry: NodeRegistry,
        manager_factory: Callable[[NodeConfig], SyncInvoker],
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._registry = registry
        self._manager_factory = manager_factory
        self._clock = clock

    def list_nodes(self) -> dict[str, Any]:
        """List registered nodes without exposing credentials."""
        return {"nodes": [node.public_dict() for node in self._registry.list()]}

    def _node_and_manager(self, node_id: str) -> tuple[NodeConfig, SyncInvoker]:
        node = self._registry.get(node_id)
        return node, self._manager_factory(node)

    def node_status(self, node_id: str) -> dict[str, Any]:
        """Read one node's host status and thermal summary."""
        node, manager = self._node_and_manager(node_id)
        system = get_system(manager)
        thermal = get_thermal(manager)
        return {
            "id": node.id,
            "address": node.address,
            "system": {
                "id": system.id,
                "name": system.name,
                "powerState": system.power_state,
                "health": system.health,
                "state": system.state,
            },
            "temperature": _temperature_summary(thermal),
            "lastPolled": _rfc3339(self._clock()),
        }

    def node_sensors(self, node_id: str) -> dict[str, Any]:
        """Read normalized chassis sensor rows for one node."""
        node, manager = self._node_and_manager(node_id)
        return {
            "id": node.id,
            "sensors": _sensor_dicts(manager),
        }

    def node_gpu_metrics(self, node_id: str) -> dict[str, Any]:
        """Read consolidated GPU metric rows for one node."""
        node, manager = self._node_and_manager(node_id)
        return {
            "id": node.id,
            "gpuMetrics": _raw_command(
                manager,
                ApiRequestType.GpuMetrics,
                "gpu-metrics",
            ),
        }

    def node_bios(
        self,
        node_id: str,
        *,
        attr_filter: str | None = None,
    ) -> dict[str, Any]:
        """Read BIOS attributes for one node."""
        node, manager = self._node_and_manager(node_id)
        return {
            "id": node.id,
            "bios": _raw_command(
                manager,
                ApiRequestType.BiosQuery,
                "bios_inventory",
                attr_filter=attr_filter or "",
                attr_only=False,
                do_deep=False,
            ),
        }
