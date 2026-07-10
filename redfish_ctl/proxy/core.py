"""Dependency-light read proxy core for Redfish fleet services."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from redfish_ctl.api import (
    RedfishApiError,
    SyncInvoker,
    ThermalStatus,
    get_sensors,
    get_system,
    get_thermal,
)
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    build_identity_dimensions,
    build_metric_samples,
)


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


def _address_host(address: str) -> str:
    text = str(address or "").strip()
    if "://" in text:
        parsed = urlparse(text)
        return parsed.hostname or text
    hostport = text.split("/", 1)[0].rsplit("@", 1)[-1]
    if hostport.startswith("[") and "]" in hostport:
        return hostport.split("]", 1)[0].lstrip("[")
    return hostport.split(":", 1)[0] or text


def _vendor_label(manager: SyncInvoker, vendor: str | None) -> str:
    if vendor:
        return vendor
    try:
        detected = getattr(manager, "redfish_vendor")
    except Exception:
        detected = None
    return str(detected or "unknown")


def _optional_command(
    manager: SyncInvoker,
    api_call: ApiRequestType,
    name: str,
    **kwargs: Any,
) -> Any:
    try:
        result = manager.sync_invoke(api_call, name, **kwargs)
    except Exception:
        return None
    if result.error:
        return None
    return result.data


def _list_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _dict_payload(data: Any) -> dict[str, Any]:
    return dict(data) if isinstance(data, dict) else {}


def _dict_rows(data: Any, key: str) -> list[dict[str, Any]]:
    rows = _dict_payload(data).get(key)
    return _list_payload(rows)


def _sample_dict(sample: MetricSample) -> dict[str, Any]:
    return {
        "metric": sample.metric,
        "value": sample.value,
        "dimensions": dict(sample.dimensions),
        "metricType": sample.metric_type,
        "unit": sample.unit,
        "timestamp": sample.timestamp,
    }


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

    def node_metric_samples(
        self,
        node_id: str,
        *,
        label_bmc_ip: str | None = None,
        vendor: str | None = None,
        do_expanded: bool = False,
    ) -> tuple[MetricSample, ...]:
        """Read one node and return exporter-compatible telemetry samples."""
        node, manager = self._node_and_manager(node_id)
        identity = build_identity_dimensions(
            label_bmc_ip or _address_host(node.address),
            vendor=_vendor_label(manager, vendor),
        )
        environment_rows = _dict_rows(
            _optional_command(
                manager,
                ApiRequestType.EnvironmentMetrics,
                "environment-metrics",
            ),
            "metrics",
        )
        thermal_rows = _dict_rows(
            _optional_command(manager, ApiRequestType.Thermal, "thermal"),
            "temperature_readings",
        )
        sensor_rows = _list_payload(
            _optional_command(
                manager,
                ApiRequestType.Sensors,
                "sensors",
                do_expanded=do_expanded,
            )
        )
        nvlink_rows = _list_payload(
            _optional_command(
                manager,
                ApiRequestType.NvLinkPorts,
                "nvlink-ports",
                do_expanded=do_expanded,
            )
        )
        metric_report_rows = _list_payload(
            _optional_command(
                manager,
                ApiRequestType.MetricReports,
                "metric-reports",
                do_expanded=do_expanded,
            )
        )
        leak_detection_rows = _dict_rows(
            _optional_command(
                manager,
                ApiRequestType.LeakDetectors,
                "leak-detectors",
            ),
            "detectors",
        )
        network_rows = _list_payload(
            _optional_command(
                manager,
                ApiRequestType.NetworkAdapters,
                "network-adapters",
                do_expanded=do_expanded,
            )
        )
        component_rows = _list_payload(
            _optional_command(
                manager,
                ApiRequestType.ComponentIntegrity,
                "component-integrity",
                do_expanded=do_expanded,
            )
        )
        return tuple(build_metric_samples(
            identity=identity,
            environment_rows=environment_rows,
            sensor_rows=sensor_rows,
            nvlink_rows=nvlink_rows,
            metric_report_rows=metric_report_rows,
            thermal_rows=thermal_rows,
            leak_detection_rows=leak_detection_rows,
            network_rows=network_rows,
            component_integrity_rows=component_rows,
        ))

    def node_metrics(
        self,
        node_id: str,
        *,
        label_bmc_ip: str | None = None,
        vendor: str | None = None,
        do_expanded: bool = False,
    ) -> dict[str, Any]:
        """Return JSON-safe exporter samples for one node."""
        samples = self.node_metric_samples(
            node_id,
            label_bmc_ip=label_bmc_ip,
            vendor=vendor,
            do_expanded=do_expanded,
        )
        return {
            "id": node_id,
            "sampleCount": len(samples),
            "samples": [_sample_dict(sample) for sample in samples],
        }
