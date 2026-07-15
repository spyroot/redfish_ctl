#!/usr/bin/env python3
"""Read-only status controller for RedfishEndpoint resources."""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

import requests

try:  # pragma: no cover - exercised only in a deployed controller image.
    import kopf
except ImportError:  # pragma: no cover - unit tests call the handler directly.
    kopf = None

from redfish_ctl.api import (
    RedfishApiError,
    SensorReading,
    SystemStatus,
    ThermalStatus,
    get_sensors,
    get_system,
    get_thermal,
)
from redfish_ctl.cmd_exceptions import AuthenticationFailed, ResourceNotFound
from redfish_ctl.kube_client import get_core_v1_api
from redfish_ctl.redfish_exceptions import (
    RedfishException,
    RedfishForbidden,
    RedfishUnauthorized,
)
from redfish_ctl.redfish_manager_base import RedfishManagerBase

REDFISH_GROUP = "redfish.ctl.dev"
REDFISH_VERSION = "v1alpha1"
REDFISH_PLURAL = "redfishendpoints"
DEFAULT_PORT = 443
DEFAULT_USERNAME = "root"

#: Fallback cadence when the CR omits ``spec.pollInterval`` and no env override
#: is set. Also the kopf timer's base firing interval (see ``base_interval_seconds``).
DEFAULT_POLL_INTERVAL_SECONDS = 30.0

#: Slack allowed when deciding a poll is "due", so a base-cadence timer fire that
#: lands a hair short of ``spec.pollInterval`` (sub-second jitter) still polls
#: instead of skipping a full cycle.
POLL_DUE_TOLERANCE_SECONDS = 1.0

#: Ceiling for the exponential backoff applied after repeated BMC failures.
MAX_BACKOFF_SECONDS = 600.0

#: Condition type surfaced on ``.status`` describing BMC reachability.
REACHABLE_CONDITION = "EndpointReachable"

#: Errors that mean "the BMC could not be read this cycle" rather than a bug in
#: the controller. These are caught, recorded on ``.status`` with a backoff, and
#: retried on the next timer fire instead of raising forever.
POLL_ERRORS: tuple[type[BaseException], ...] = (
    RedfishApiError,
    RedfishException,
    AuthenticationFailed,
    ResourceNotFound,
    requests.exceptions.RequestException,
    ConnectionError,
    TimeoutError,
    OSError,
)

ManagerFactory = Callable[..., Any]

#: Manager class the handler builds per poll. Indirected through a module global
#: so tests can substitute a fake BMC facade without a real network.
MANAGER_FACTORY: ManagerFactory = RedfishManagerBase


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


_INTERVAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$")
_UNIT_SECONDS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_interval_seconds(text: Any, default: float) -> float:
    """Parse a ``spec.pollInterval`` string ("30s"/"5m"/"1h") into seconds.

    Returns ``default`` for missing, malformed, or non-positive values, so a bad
    CR field degrades to the base cadence rather than breaking the poll loop.
    """
    if text is None:
        return default
    if isinstance(text, bool):
        return default
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else default
    match = _INTERVAL_RE.match(str(text))
    if not match:
        return default
    value = float(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    return value if value > 0 else default


#: Env var the deployment/Helm chart sets from ``controller.pollInterval`` to
#: retune the controller's base timer cadence. The old handler hard-coded
#: ``interval=30`` and never read it, so this knob was wired but dead.
POLL_INTERVAL_ENV = "REDFISH_CONTROLLER_POLL_INTERVAL"


def base_interval_seconds() -> float:
    """The kopf timer's base firing cadence, overridable via env.

    Reads ``REDFISH_CONTROLLER_POLL_INTERVAL`` (a duration like ``30s``/``1m``),
    the value the Helm chart's ``controller.pollInterval`` already renders into
    the controller container, so an operator can retune the deployment without
    editing code. Per-CR ``spec.pollInterval`` still governs each endpoint on
    top of this floor.
    """
    return parse_interval_seconds(
        os.environ.get(POLL_INTERVAL_ENV),
        DEFAULT_POLL_INTERVAL_SECONDS,
    )


def _parse_rfc3339(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def poll_due(
    spec: Mapping[str, Any],
    status: Mapping[str, Any],
    now: datetime,
) -> bool:
    """Decide whether this timer fire should poll the BMC.

    Honors two per-CR gates written into ``.status``:

    * ``nextPollAfter`` — an exponential backoff set after a failed poll, so an
      unreachable BMC is not hammered on every base-cadence timer fire.
    * ``spec.pollInterval`` vs ``lastPolled`` — lets an endpoint poll slower than
      the base cadence. (The base cadence is the floor; a CR asking to poll
      faster than the deployment's timer is bounded by it.)

    A CR with no prior successful poll is always due.
    """
    next_after = _parse_rfc3339(status.get("nextPollAfter"))
    if next_after is not None and now < next_after:
        return False
    last_polled = _parse_rfc3339(status.get("lastPolled"))
    if last_polled is None:
        return True
    desired = parse_interval_seconds(spec.get("pollInterval"), base_interval_seconds())
    elapsed = (now - last_polled).total_seconds()
    return elapsed + POLL_DUE_TOLERANCE_SECONDS >= desired


def backoff_seconds(failures: int, base: float, cap: float = MAX_BACKOFF_SECONDS) -> float:
    """Exponential backoff for consecutive failures, capped.

    ``failures`` is 1 for the first failure. Delay doubles each failure from the
    base cadence up to ``cap``.
    """
    if failures < 1:
        return base
    delay = base * (2 ** (failures - 1))
    return min(delay, cap)


def _condition(
    condition_type: str,
    status: bool,
    reason: str,
    *,
    message: str,
    changed_at: datetime,
) -> dict[str, str]:
    condition = {
        "type": condition_type,
        "status": "True" if status else "False",
        "reason": reason,
        "lastTransitionTime": _rfc3339(changed_at),
    }
    if message:
        condition["message"] = message
    return condition


def _classify_poll_error(exc: BaseException) -> tuple[str, str]:
    """Map a BMC read failure to a (reason, message) for the status condition."""
    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, (RedfishUnauthorized, RedfishForbidden, AuthenticationFailed)):
        return "AuthenticationFailed", message
    if isinstance(exc, ResourceNotFound):
        return "ResourceNotFound", message
    if isinstance(
        exc,
        (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, TimeoutError),
    ):
        return "Timeout", message
    if isinstance(exc, (requests.exceptions.ConnectionError, ConnectionError, OSError)):
        return "BMCUnreachable", message
    return "PollFailed", message


def build_error_status(
    prev_status: Mapping[str, Any],
    exc: BaseException,
    *,
    now: datetime,
    base_interval: float,
) -> dict[str, Any]:
    """Status patch for a failed poll.

    Deliberately omits ``powerState``/``health``/``temperature``/``lastPolled``:
    a RedfishEndpoint ``.status`` update is a JSON merge-patch (RFC 7386), so
    omitting those keys preserves the last successful readings while recording
    the failure and a backoff. ``lastPolled`` stays at the last *successful*
    poll, and the failure is explained by the condition + ``lastError`` rather
    than freezing silently.
    """
    failures = _int(prev_status.get("consecutiveFailures")) + 1
    reason, message = _classify_poll_error(exc)
    delay = backoff_seconds(failures, base_interval)
    return {
        "conditions": [
            _condition(
                REACHABLE_CONDITION,
                False,
                reason,
                message=message,
                changed_at=now,
            )
        ],
        "consecutiveFailures": failures,
        "lastError": message,
        "nextPollAfter": _rfc3339(now + timedelta(seconds=delay)),
    }


def build_success_status(
    readings: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Merge the core readings with a healthy condition and cleared failure fields."""
    status = dict(readings)
    status["conditions"] = [
        _condition(
            REACHABLE_CONDITION,
            True,
            "PollSucceeded",
            message="",
            changed_at=now,
        )
    ]
    status["consecutiveFailures"] = 0
    # Null clears the field via merge-patch when the endpoint recovers.
    status["lastError"] = None
    status["nextPollAfter"] = None
    return status


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _close_manager(manager: Any) -> None:
    """Best-effort close of the manager's pooled HTTP session.

    ``RedfishManagerBase`` lazily caches a keep-alive ``requests.Session`` (with
    its urllib3 connection pool) in ``_session_cache``. The controller builds one
    manager per poll, so at fleet scale unclosed sessions would leak sockets/FDs.
    """
    session = getattr(manager, "_session_cache", None)
    if session is None:
        return
    try:
        session.close()
    except Exception:  # pragma: no cover - close must never break a poll.
        pass


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
    manager_factory: ManagerFactory = RedfishManagerBase,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Read a BMC through the facade and return a status patch payload.

    Always closes the per-poll manager's pooled session, even on error, so a
    fleet of endpoints does not leak sockets one failed/slow poll at a time.
    """
    manager = _make_manager(spec, credentials or {}, manager_factory)
    try:
        system = get_system(manager)
        sensors = get_sensors(manager)
        thermal = get_thermal(manager)
        return build_status(system, sensors, thermal, polled_at=polled_at)
    finally:
        _close_manager(manager)


def _decode_secret_value(data: Mapping[str, str], key: str) -> str | None:
    encoded = data.get(key)
    if not encoded:
        return None
    return base64.b64decode(encoded).decode("utf-8")


def load_secret_credentials(
    namespace: str | None,
    secret_ref: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Read credentials from the Secret named by spec.secretRef when available.

    Uses the process-wide client from :mod:`redfish_ctl.kube_client`, which
    loads the kube config once and is safe to share across kopf's handler
    threads. Falls back to empty credentials whenever a client is unavailable
    (kubernetes not installed, or no in-cluster/local config), matching the
    offline behaviour the test suite relies on.
    """
    if not namespace or not secret_ref or not secret_ref.get("name"):
        return {}
    try:
        api = get_core_v1_api()
    except Exception:  # pragma: no cover - kubernetes/config unavailable offline.
        return {}

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
    status: Mapping[str, Any] | None = None,
    force: bool = False,
    **_: Any,
) -> None:
    """Kopf callback that updates only the RedfishEndpoint status subresource.

    Status is written through the injected ``patch`` object; the handler
    returns ``None`` on purpose. Returning a value makes kopf persist it under
    ``status.poll_redfish_endpoint``, a field the structural CRD schema
    rejects, which surfaces a "merge-patching finished with inconsistencies"
    warning on every poll.

    Per-CR ``spec.pollInterval`` and post-failure backoff are honored: the timer
    fires at the deployment's base cadence, and :func:`poll_due` decides whether
    this fire actually polls. Lifecycle events (create/update) pass ``force=True``
    so an operator's spec change — e.g. fixing a bad address mid-backoff — takes
    effect immediately instead of waiting out the interval/backoff. A BMC read
    failure is caught, recorded on ``.status`` with an exponential backoff, and
    retried next cycle instead of raising forever; the last successful readings
    are preserved (merge-patch).
    """
    current_status = status if status is not None else _mapping(body).get("status")
    current_status = _mapping(current_status)
    now = _utc_now()
    if not force and not poll_due(spec, current_status, now):
        return None

    credentials = load_secret_credentials(namespace, spec.get("secretRef"))
    base = base_interval_seconds()
    try:
        readings = poll_endpoint(
            spec,
            credentials=credentials,
            manager_factory=MANAGER_FACTORY,
            polled_at=now,
        )
    except POLL_ERRORS as exc:
        new_status = build_error_status(
            current_status, exc, now=now, base_interval=base
        )
        if logger is not None:
            logger.warning(
                "RedfishEndpoint %s/%s poll failed (%s): %s",
                namespace or "",
                name or "",
                new_status["consecutiveFailures"],
                new_status["lastError"],
            )
    else:
        new_status = build_success_status(readings, now=now)
        if logger is not None:
            logger.info("polled RedfishEndpoint %s/%s", namespace or "", name or "")

    if patch is not None:
        patch.setdefault("status", {}).update(new_status)
    return None


def poll_on_change(**kwargs: Any) -> None:  # pragma: no cover - runtime kopf wiring.
    """Create/update entrypoint: poll immediately, bypassing the cadence gate."""
    return poll_redfish_endpoint(force=True, **kwargs)


if kopf is not None:  # pragma: no cover - decorator wiring is runtime-only.
    # Lifecycle events poll now (force=True); the timer polls on the per-CR
    # cadence via poll_due. Distinct functions so kopf registers distinct
    # handler ids for the change causes vs the timer.
    kopf.on.create(REDFISH_GROUP, REDFISH_VERSION, REDFISH_PLURAL)(poll_on_change)
    kopf.on.update(REDFISH_GROUP, REDFISH_VERSION, REDFISH_PLURAL)(poll_on_change)
    kopf.timer(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
        interval=base_interval_seconds(),
        sharp=True,
    )(poll_redfish_endpoint)
