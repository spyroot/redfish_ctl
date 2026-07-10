#!/usr/bin/env python3
"""Read-only status controller for RedfishEndpoint resources."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

try:  # pragma: no cover - exercised only in a deployed controller image.
    import kopf
except ImportError:  # pragma: no cover - unit tests call the handler directly.
    kopf = None

from redfish_ctl.api import (
    SensorReading,
    SystemStatus,
    ThermalStatus,
    get_sensors,
    get_system,
    get_thermal,
)
from redfish_ctl.idrac_manager import IDracManager

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


def build_status(
    system: SystemStatus,
    sensors: tuple[SensorReading, ...],
    thermal: ThermalStatus,
    *,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Return the RedfishEndpoint status object from typed read results."""
    return {
        "powerState": system.power_state,
        "health": system.health or _health_from_sensors(sensors),
        "temperature": _temperature_summary(thermal),
        "lastPolled": _rfc3339(polled_at or _utc_now()),
    }


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


def poll_endpoint(
    spec: Mapping[str, Any],
    *,
    credentials: Mapping[str, str] | None = None,
    manager_factory: ManagerFactory = IDracManager,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Read a BMC through the facade and return a status patch payload."""
    manager = _make_manager(spec, credentials or {}, manager_factory)
    system = get_system(manager)
    sensors = get_sensors(manager)
    thermal = get_thermal(manager)
    return build_status(system, sensors, thermal, polled_at=polled_at)


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
) -> dict[str, Any]:
    """Kopf callback that updates only the RedfishEndpoint status subresource."""
    credentials = load_secret_credentials(namespace, spec.get("secretRef"))
    status = poll_endpoint(spec, credentials=credentials)
    if patch is not None:
        patch.setdefault("status", {}).update(status)
    if logger is not None:
        logger.info("polled RedfishEndpoint %s/%s", namespace or "", name or "")
    return {"status": status}


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
