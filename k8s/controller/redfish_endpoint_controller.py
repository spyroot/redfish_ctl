#!/usr/bin/env python3
"""Read-only status controller for RedfishEndpoint resources."""

from __future__ import annotations

import base64
import logging
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
    NetworkFirmwareStatus,
    RedfishApiError,
    SensorReading,
    SystemStatus,
    ThermalStatus,
    get_network_firmware,
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
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.telemetry import tracing

_LOGGER = logging.getLogger(__name__)

# Cap the number of firmware components written to status to keep the CR small;
# a NIC/DPU set is a handful, but guard against a pathological host.
_MAX_FIRMWARE_COMPONENTS = 64

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
MANAGER_FACTORY: ManagerFactory = IDracManager


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    :return: current time in UTC.
    """
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    """Format a datetime as an RFC 3339 UTC string with a ``Z`` suffix.

    Naive values are assumed to be UTC.

    :param value: datetime to format.
    :return: RFC 3339 timestamp string (seconds precision, ``Z`` zone).
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _number(value: int | float | str | None) -> float | None:
    """Coerce a numeric-like value to ``float``, or ``None`` if it cannot.

    Booleans and unparsable strings return ``None`` rather than a number.

    :param value: value to coerce (int, float, numeric string, or ``None``).
    :return: the value as a float, or ``None`` when not numeric.
    """
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
    """Summarize thermal readings into a count and the hottest temperature.

    :param thermal: thermal read result whose ``temperatures`` are inspected.
    :return: dict with ``count`` of valid readings and ``maxCelsius`` (or ``None``).
    """
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
    """Return the worst health across sensors, or ``None`` if none report health.

    :param sensors: sensor readings to reduce.
    :return: the most severe health string (Critical > Warning > OK), or ``None``.
    """
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

    :param network_firmware: typed NIC/DPU firmware read result.
    :return: the ``status.networkFirmware`` block (counts, distinct versions,
        and a capped list of components).
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
    """Return the RedfishEndpoint status object from typed read results.

    :param system: typed system read result (power state, health).
    :param sensors: sensor readings, used as a health fallback.
    :param thermal: thermal read result for the temperature summary.
    :param network_firmware: optional NIC/DPU firmware; adds the
        ``networkFirmware`` block when present.
    :param polled_at: timestamp recorded as ``lastPolled``; defaults to now.
    :return: the ``.status`` object for the RedfishEndpoint CR.
    """
    status: dict[str, Any] = {
        "powerState": system.power_state,
        "health": system.health or _health_from_sensors(sensors),
        "temperature": _temperature_summary(thermal),
        "lastPolled": _rfc3339(polled_at or _utc_now()),
    }
    if network_firmware is not None:
        status["networkFirmware"] = _network_firmware_summary(network_firmware)
    return status


_INTERVAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$")
_UNIT_SECONDS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_interval_seconds(text: Any, default: float) -> float:
    """Parse a ``spec.pollInterval`` string ("30s"/"5m"/"1h") into seconds.

    Returns ``default`` for missing, malformed, or non-positive values, so a bad
    CR field degrades to the base cadence rather than breaking the poll loop.

    :param text: interval value (a duration string, number, or ``None``).
    :param default: seconds to return when ``text`` is missing or invalid.
    :return: the parsed interval in seconds, or ``default``.
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
OTLP_TRACES_ENV = "REDFISH_CONTROLLER_OTLP_TRACES"
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def base_interval_seconds() -> float:
    """The kopf timer's base firing cadence, overridable via env.

    Reads ``REDFISH_CONTROLLER_POLL_INTERVAL`` (a duration like ``30s``/``1m``),
    the value the Helm chart's ``controller.pollInterval`` already renders into
    the controller container, so an operator can retune the deployment without
    editing code. Per-CR ``spec.pollInterval`` still governs each endpoint on
    top of this floor.

    :return: the base timer cadence in seconds.
    """
    return parse_interval_seconds(
        os.environ.get(POLL_INTERVAL_ENV),
        DEFAULT_POLL_INTERVAL_SECONDS,
    )


def _controller_otlp_traces_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether controller OTLP tracing is enabled by env.

    The ``REDFISH_CONTROLLER_OTLP_TRACES`` env var, rendered by the deployment
    and Helm chart, enables the controller's OTLP span pipeline when set to a
    true-like value.

    :param environ: environment mapping to read; defaults to ``os.environ``.
    :return: True when controller tracing should be set up.
    """
    values = os.environ if environ is None else environ
    return str(values.get(OTLP_TRACES_ENV, "")).strip().lower() in _TRUE_ENV_VALUES


def setup_controller_tracing(environ: Mapping[str, str] | None = None) -> None:
    """Set up the controller OTLP span pipeline when enabled by env.

    :param environ: environment mapping to read; defaults to ``os.environ``.
    """
    if _controller_otlp_traces_enabled(environ):
        tracing.setup_otlp("redfish-controller")


def _server_address(spec: Mapping[str, Any]) -> str:
    """Return the BMC host value used on controller root spans.

    :param spec: RedfishEndpoint spec.
    :return: host from ``spec.address``, with URL schemes stripped when present.
    """
    raw_address = str(spec.get("address") or "").strip()
    parsed = urlsplit(raw_address)
    if parsed.scheme in {"http", "https"}:
        return parsed.hostname or parsed.netloc or raw_address
    return raw_address


def _set_span_attribute(span: Any, key: str, value: Any) -> None:
    """Set a span attribute when tracing is enabled and a value is present.

    :param span: current span, or None when tracing is disabled.
    :param key: span attribute key.
    :param value: span attribute value.
    """
    if span is not None and value not in (None, ""):
        span.set_attribute(key, value)


def _set_controller_span_attributes(
    span: Any,
    spec: Mapping[str, Any],
    *,
    namespace: str | None,
    name: str | None,
    resource_kind: str,
) -> None:
    """Attach bounded Kubernetes/BMC identity to a controller root span.

    :param span: current operation span, or None when tracing is disabled.
    :param spec: resource spec containing the endpoint address.
    :param namespace: Kubernetes namespace.
    :param name: Kubernetes object name.
    :param resource_kind: Kubernetes custom resource kind.
    """
    _set_span_attribute(span, "server.address", _server_address(spec))
    _set_span_attribute(span, "k8s.namespace.name", namespace)
    _set_span_attribute(span, "k8s.resource.name", name)
    _set_span_attribute(span, "k8s.resource.kind", resource_kind)


def _parse_rfc3339(text: Any) -> datetime | None:
    """Parse an RFC 3339 timestamp into a UTC-aware datetime.

    :param text: timestamp string to parse; non-strings yield ``None``.
    :return: the parsed timezone-aware datetime, or ``None`` when unparsable.
    """
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

    :param spec: the CR spec, read for ``pollInterval``.
    :param status: the CR ``.status``, read for ``nextPollAfter``/``lastPolled``.
    :param now: current time used for the due/backoff comparison.
    :return: ``True`` when the BMC should be polled this fire, else ``False``.
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

    :param failures: count of consecutive failures (1 for the first).
    :param base: base delay in seconds that doubles per failure.
    :param cap: maximum delay in seconds.
    :return: the backoff delay in seconds, capped at ``cap``.
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
    """Build a Kubernetes-style status condition entry.

    :param condition_type: the condition ``type`` value.
    :param status: whether the condition holds, rendered as ``"True"``/``"False"``.
    :param reason: machine-readable reason code.
    :param message: human-readable detail; omitted when empty.
    :param changed_at: transition time recorded as ``lastTransitionTime``.
    :return: the condition dict.
    """
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
    """Map a BMC read failure to a (reason, message) for the status condition.

    :param exc: the exception raised while reading the BMC.
    :return: tuple of (reason code, message) for the status condition.
    """
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

    :param prev_status: the current ``.status``, read for ``consecutiveFailures``.
    :param exc: the exception raised during the failed poll.
    :param now: current time, used for the condition and backoff deadline.
    :param base_interval: base cadence the exponential backoff builds on.
    :return: a partial ``.status`` merge patch recording the failure and backoff.
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
    """Merge the core readings with a healthy condition and cleared failure fields.

    :param readings: the status readings from a successful poll.
    :param now: current time recorded on the reachable condition.
    :return: the full ``.status`` object for a successful poll.
    """
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
    """Coerce a value to ``int``, returning ``0`` when it cannot be parsed.

    :param value: value to coerce.
    :return: the value as an int, or ``0`` on failure.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else an empty mapping.

    :param value: candidate mapping.
    :return: the mapping, or an empty dict when ``value`` is not one.
    """
    return value if isinstance(value, Mapping) else {}


def _close_manager(manager: Any) -> None:
    """Best-effort close of the manager's pooled HTTP session.

    ``IDracManager`` lazily caches a keep-alive ``requests.Session`` (with
    its urllib3 connection pool) in ``_session_cache``. The controller builds one
    manager per poll, so at fleet scale unclosed sessions would leak sockets/FDs.

    :param manager: the per-poll manager whose cached session is closed.
    """
    session = getattr(manager, "_session_cache", None)
    if session is None:
        return
    try:
        session.close()
    except Exception:  # pragma: no cover - close must never break a poll.
        pass


def _manager_address(spec: Mapping[str, Any]) -> tuple[str, bool]:
    """Resolve the BMC host and HTTP flag from ``spec.address``.

    Accepts either a bare host/IP (with ``spec.port``) or an ``http(s)://`` URL.

    :param spec: the CR spec, read for ``address``, ``port``, and ``insecure``.
    :return: tuple of (host address, whether to use plain HTTP).
    :raises ValueError: when ``spec.address`` is empty.
    """
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
    """Build a Redfish manager for the endpoint from spec and credentials.

    :param spec: the CR spec, read for address, port, and ``insecure``.
    :param credentials: username/password mapping for BMC auth.
    :param manager_factory: callable that constructs the manager (indirected
        for tests).
    :return: the constructed manager instance.
    """
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

    :param manager: the BMC manager to read NIC/DPU firmware from.
    :return: the network firmware read result, or ``None`` when unavailable.
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
    manager_factory: ManagerFactory = IDracManager,
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    """Read a BMC through the facade and return a status patch payload.

    Always closes the per-poll manager's pooled session, even on error, so a
    fleet of endpoints does not leak sockets one failed/slow poll at a time.

    :param spec: the CR spec identifying and configuring the endpoint.
    :param credentials: username/password mapping for BMC auth.
    :param manager_factory: callable that constructs the manager.
    :param polled_at: timestamp recorded as ``lastPolled``; defaults to now.
    :return: the ``.status`` object built from the read results.
    """
    manager = _make_manager(spec, credentials or {}, manager_factory)
    try:
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
    finally:
        _close_manager(manager)


def _decode_secret_value(data: Mapping[str, str], key: str) -> str | None:
    """Decode a base64 Secret field, or ``None`` when the key is absent/empty.

    :param data: the Secret ``data`` mapping (base64-encoded values).
    :param key: the field name to decode.
    :return: the decoded UTF-8 value, or ``None`` when missing.
    """
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

    :param namespace: namespace of the CR (and its Secret); empty skips the read.
    :param secret_ref: ``spec.secretRef`` naming the Secret and its keys.
    :return: mapping with ``username``/``password`` when found, else empty.
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

    :param spec: the CR spec identifying and configuring the endpoint.
    :param body: the full CR body; used to read ``.status`` when ``status`` is
        not passed.
    :param namespace: namespace of the CR, for the Secret read and logging.
    :param name: name of the CR, for logging.
    :param logger: kopf logger; poll outcomes are logged when provided.
    :param patch: kopf patch object the new ``.status`` is written into.
    :param status: the current ``.status`` passed by kopf; falls back to
        ``body`` when ``None``.
    :param force: when ``True``, poll immediately and bypass the cadence gate.
    """
    with tracing.operation_span("k8s.redfish_endpoint.reconcile") as span:
        _set_controller_span_attributes(
            span,
            spec,
            namespace=namespace,
            name=name,
            resource_kind="RedfishEndpoint",
        )
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
            tracing.record_exception(span, exc)
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
    """Create/update entrypoint: poll immediately, bypassing the cadence gate.

    :return: ``None`` (delegates to :func:`poll_redfish_endpoint`).
    """
    return poll_redfish_endpoint(force=True, **kwargs)


if kopf is not None:  # pragma: no cover - decorator wiring is runtime-only.
    setup_controller_tracing()
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
