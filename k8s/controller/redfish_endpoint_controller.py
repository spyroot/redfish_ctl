#!/usr/bin/env python3
"""Read-only status controller for RedfishEndpoint resources."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

try:  # pragma: no cover - exercised only in a deployed controller image.
    import kopf
except ImportError:  # pragma: no cover - unit tests call the handler directly.
    kopf = None

from redfish_ctl.api import (
    NetworkFirmwareStatus,
    SensorReading,
    SystemStatus,
    ThermalStatus,
    get_network_firmware,
    get_sensors,
    get_system,
    get_thermal,
)
from redfish_ctl.redfish_manager_base import RedfishManagerBase

_LOGGER = logging.getLogger(__name__)

# Cap the number of firmware components written to status to keep the CR small;
# a NIC/DPU set is a handful, but guard against a pathological host.
_MAX_FIRMWARE_COMPONENTS = 64

REDFISH_GROUP = "redfish.ctl.dev"
REDFISH_VERSION = "v1alpha1"
REDFISH_PLURAL = "redfishendpoints"
DEFAULT_PORT = 443
DEFAULT_USERNAME = "root"

ManagerFactory = Callable[..., Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _number(value: int | float | str | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return None


def _temperature_summary(thermal: ThermalStatus) -> dict[str, int | float | None]:
    values = [
        reading
        for row in thermal.temperatures
        if (reading := _number(row.reading_celsius)) is not None
    ]
    return {
        "count": len(values),
        "maxCelsius": max(values) if values else None,
    }


def _health_from_sensors(sensors: tuple[SensorReading, ...]) -> str | None:
    rank = {
        "OK": 0,
        "Warning": 1,
        "Critical": 2,
    }
    health_values = [sensor.health for sensor in sensors if sensor.health]
    if not health_values:
        return None
    return max(health_values, key=lambda value: rank.get(value, -1))


def _network_firmware_summary(
    network_firmware: NetworkFirmwareStatus,
) -> dict[str, Any]:
    """Shape a NetworkFirmwareStatus into the CR status.networkFirmware block.

    ``distinctVersions`` is the fleet drift signal: one version across the fleet
    means every node's NICs run the same firmware; more than one flags drift.
    """
    summary = network_firmware.summary
    components = [
        {
            "id": fw.id,
            "deviceClass": fw.device_class,
            "version": fw.version,
            "updateable": bool(fw.updateable) if fw.updateable is not None else None,
        }
        for fw in network_firmware.firmware[:_MAX_FIRMWARE_COMPONENTS]
    ]
    distinct = [str(v) for v in summary.get("distinct_versions", []) if v is not None]
    return {
        "adapterCount": int(summary.get("adapter_count", 0) or 0),
        "nicCount": int(summary.get("nic_count", 0) or 0),
        "dpuCount": int(summary.get("dpu_count", 0) or 0),
        "firmwareCount": int(summary.get("firmware_count", 0) or 0),
        "updateableCount": int(summary.get("updateable_count", 0) or 0),
        "distinctVersions": distinct,
        "components": components,
    }


def build_status(
    system: SystemStatus,
    sensors: tuple[SensorReading, ...],
    thermal: ThermalStatus,
    *,
    network_firmware: NetworkFirmwareStatus | None = None,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Return the RedfishEndpoint status object from typed read results."""
    status: dict[str, Any] = {
        "powerState": system.power_state,
        "health": system.health or _health_from_sensors(sensors),
        "temperature": _temperature_summary(thermal),
        "lastPolled": _rfc3339(polled_at or _utc_now()),
    }
    if network_firmware is not None:
        status["networkFirmware"] = _network_firmware_summary(network_firmware)
    return status


def _manager_address(spec: Mapping[str, Any]) -> tuple[str, bool]:
    raw_address = str(spec.get("address") or "").strip()
    if not raw_address:
        raise ValueError("RedfishEndpoint spec.address is required")

    parsed = urlsplit(raw_address)
    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc or parsed.path
        return host.rstrip("/"), parsed.scheme == "http"

    port = int(spec.get("port") or DEFAULT_PORT)
    return raw_address, port != DEFAULT_PORT and bool(spec.get("insecure", False))


def _make_manager(
    spec: Mapping[str, Any],
    credentials: Mapping[str, str],
    manager_factory: ManagerFactory,
) -> Any:
    address, is_http = _manager_address(spec)
    return manager_factory(
        idrac_ip=address,
        idrac_username=credentials.get("username", DEFAULT_USERNAME),
        idrac_password=credentials.get("password", ""),
        idrac_port=int(spec.get("port") or DEFAULT_PORT),
        insecure=bool(spec.get("insecure", True)),
        is_http=is_http,
        is_debug=False,
    )


def _safe_network_firmware(manager: Any) -> NetworkFirmwareStatus | None:
    """Read NIC/DPU firmware, tolerating a BMC that exposes no network firmware.

    A host without NetworkAdapters or FirmwareInventory (or one that errors on
    that walk) must not fail the whole poll — power/health/thermal still land.
    The failure is logged (not silent) so a persistent NIC-firmware read problem
    is diagnosable; on failure the prior status.networkFirmware is left in place
    by the merge patch rather than being cleared.
    """
    try:
        return get_network_firmware(manager)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, but leave a trace
        _LOGGER.warning("network-firmware read failed, leaving prior value: %s", exc)
        return None


def poll_endpoint(
    spec: Mapping[str, Any],
    *,
    credentials: Mapping[str, str] | None = None,
    manager_factory: ManagerFactory = RedfishManagerBase,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Read a BMC through the facade and return a status patch payload."""
    manager = _make_manager(spec, credentials or {}, manager_factory)
    system = get_system(manager)
    sensors = get_sensors(manager)
    thermal = get_thermal(manager)
    network_firmware = _safe_network_firmware(manager)
    return build_status(
        system,
        sensors,
        thermal,
        network_firmware=network_firmware,
        polled_at=polled_at,
    )


def _decode_secret_value(data: Mapping[str, str], key: str) -> str | None:
    encoded = data.get(key)
    if not encoded:
        return None
    return base64.b64decode(encoded).decode("utf-8")


def load_secret_credentials(
    namespace: str | None,
    secret_ref: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Read credentials from the Secret named by spec.secretRef when available."""
    if not namespace or not secret_ref or not secret_ref.get("name"):
        return {}
    try:  # pragma: no cover - requires a Kubernetes client in-cluster or locally.
        from kubernetes import client, config
    except ImportError:  # pragma: no cover - controller images carry this dependency.
        return {}

    try:  # pragma: no cover - not exercised by offline tests.
        config.load_incluster_config()
    except Exception:
        try:
            config.load_kube_config()
        except Exception:
            return {}

    api = client.CoreV1Api()
    secret = api.read_namespaced_secret(str(secret_ref["name"]), namespace)
    data = secret.data or {}
    username_key = str(secret_ref.get("usernameKey") or "username")
    password_key = str(secret_ref.get("passwordKey") or "password")
    credentials: dict[str, str] = {}
    username = _decode_secret_value(data, username_key)
    password = _decode_secret_value(data, password_key)
    if username is not None:
        credentials["username"] = username
    if password is not None:
        credentials["password"] = password
    return credentials


def poll_redfish_endpoint(
    spec: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
    namespace: str | None = None,
    name: str | None = None,
    logger: Any | None = None,
    patch: MutableMapping[str, Any] | None = None,
    **_: Any,
) -> None:
    """Kopf callback that updates only the RedfishEndpoint status subresource.

    Status is written through the injected ``patch`` object; the handler
    returns ``None`` on purpose. Returning a value makes kopf persist it under
    ``status.poll_redfish_endpoint``, a field the structural CRD schema
    rejects, which surfaces a "merge-patching finished with inconsistencies"
    warning on every poll.
    """
    credentials = load_secret_credentials(namespace, spec.get("secretRef"))
    status = poll_endpoint(spec, credentials=credentials)
    if patch is not None:
        patch.setdefault("status", {}).update(status)
    if logger is not None:
        logger.info("polled RedfishEndpoint %s/%s", namespace or "", name or "")


if kopf is not None:  # pragma: no cover - decorator wiring is runtime-only.
    poll_redfish_endpoint = kopf.on.create(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
    )(poll_redfish_endpoint)
    poll_redfish_endpoint = kopf.on.update(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
    )(poll_redfish_endpoint)
    poll_redfish_endpoint = kopf.timer(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
        interval=30,
        sharp=True,
    )(poll_redfish_endpoint)
