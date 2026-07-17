#!/usr/bin/env python3
"""Desired-state controller for RedfishNodeProfile resources."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

try:  # pragma: no cover - exercised only in a deployed controller image.
    import kopf
except ImportError:  # pragma: no cover - unit tests call the handler directly.
    kopf = None

from redfish_ctl.kube_client import get_core_v1_api
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.telemetry import tracing

REDFISH_GROUP = "redfish.ctl.dev"
REDFISH_VERSION = "v1alpha1"
REDFISH_PLURAL = "redfishnodeprofiles"
DEFAULT_PORT = 443
DEFAULT_USERNAME = "root"
OTLP_TRACES_ENV = "REDFISH_CONTROLLER_OTLP_TRACES"
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}

ManagerFactory = Callable[..., Any]
ReconcileFunc = Callable[..., Any]


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


def _server_address(endpoint: Mapping[str, Any]) -> str:
    """Return the BMC host value used on controller root spans.

    :param endpoint: RedfishNodeProfile ``spec.endpoint`` mapping.
    :return: host from ``endpoint.address``, with URL schemes stripped when present.
    """
    raw_address = str(endpoint.get("address") or "").strip()
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
    endpoint: Mapping[str, Any],
    *,
    namespace: str | None,
    name: str | None,
    resource_kind: str,
) -> None:
    """Attach bounded Kubernetes/BMC identity to a controller root span.

    :param span: current operation span, or None when tracing is disabled.
    :param endpoint: endpoint mapping from the resource spec.
    :param namespace: Kubernetes namespace.
    :param name: Kubernetes object name.
    :param resource_kind: Kubernetes custom resource kind.
    """
    _set_span_attribute(span, "server.address", _server_address(endpoint))
    _set_span_attribute(span, "k8s.namespace.name", namespace)
    _set_span_attribute(span, "k8s.resource.name", name)
    _set_span_attribute(span, "k8s.resource.kind", resource_kind)


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


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else an empty mapping.

    :param value: candidate mapping.
    :return: the mapping, or an empty dict when ``value`` is not one.
    """
    return value if isinstance(value, Mapping) else {}


def _condition(
    condition_type: str,
    status: bool | None,
    reason: str,
    *,
    message: str = "",
    changed_at: datetime,
) -> dict[str, str]:
    """Build a Kubernetes-style status condition entry.

    :param condition_type: the condition ``type`` value.
    :param status: tri-state condition value rendered as
        ``"True"``/``"False"``/``"Unknown"`` (``None`` means unknown).
    :param reason: machine-readable reason code.
    :param message: human-readable detail; omitted when empty.
    :param changed_at: transition time recorded as ``lastTransitionTime``.
    :return: the condition dict.
    """
    if status is True:
        status_text = "True"
    elif status is False:
        status_text = "False"
    else:
        status_text = "Unknown"
    condition = {
        "type": condition_type,
        "status": status_text,
        "reason": reason,
        "lastTransitionTime": _rfc3339(changed_at),
    }
    if message:
        condition["message"] = message
    return condition


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a shallow ``dict`` copy of a mapping, else an empty dict.

    :param value: candidate mapping.
    :return: a dict copy of ``value``, or an empty dict when not a mapping.
    """
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _planned_step(step: Any) -> dict[str, Any]:
    """Shape a planned reconcile step into its CR status representation.

    :param step: a planned step object (kind/required/description/preview).
    :return: dict with the step's ``kind``, ``required``, ``description``, and
        ``preview``.
    """
    return {
        "kind": str(getattr(step, "kind", "")),
        "required": bool(getattr(step, "required", False)),
        "description": str(getattr(step, "description", "")),
        "preview": _as_dict(getattr(step, "preview", {})),
    }


def _applied_change(change: Any) -> dict[str, Any]:
    """Shape an applied reconcile change into its CR status representation.

    :param change: an applied change object (kind/changed/result).
    :return: dict with the change's ``kind``, ``changed``, and ``result``.
    """
    return {
        "kind": str(getattr(change, "kind", "")),
        "changed": bool(getattr(change, "changed", False)),
        "result": _as_dict(getattr(change, "result", {})),
    }


def plan_hash(planned_steps: list[dict[str, Any]]) -> str | None:
    """Hash the required steps of a plan for approval matching.

    Only ``required`` steps are hashed, so an unchanged plan yields a stable
    digest an operator can approve.

    :param planned_steps: the planned steps from a dry-run reconcile.
    :return: a SHA-256 hex digest of the required steps, or ``None`` when none
        are required.
    """
    required_steps = [step for step in planned_steps if step.get("required")]
    if not required_steps:
        return None
    encoded = json.dumps(
        required_steps,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approval_reason(
    *,
    approved: bool,
    current_plan_hash: str | None,
    approved_plan_hash: str | None,
    consumed_plan_hash: str | None,
) -> str:
    """Classify why a plan is or is not approved, as a condition reason code.

    :param approved: whether the current plan is approved to apply.
    :param current_plan_hash: hash of the current plan's required steps.
    :param approved_plan_hash: plan hash the spec approves, if any.
    :param consumed_plan_hash: plan hash already applied, if any.
    :return: a reason code (``Approved``/``ApprovalConsumed``/
        ``ApprovalHashMismatch``/``ApprovalRequired``).
    """
    if approved:
        return "Approved"
    if current_plan_hash and consumed_plan_hash == current_plan_hash:
        return "ApprovalConsumed"
    if approved_plan_hash and approved_plan_hash != current_plan_hash:
        return "ApprovalHashMismatch"
    return "ApprovalRequired"


def build_status(
    result: Any,
    *,
    approved: bool,
    approved_plan_hash: str | None = None,
    plan_hash_value: str | None = None,
    consumed_plan_hash: str | None = None,
    reconciled_at: datetime | None = None,
) -> dict[str, Any]:
    """Return the RedfishNodeProfile status object from a reconcile result.

    :param result: the reconcile result (its ``steps``/``applied``/``dry_run``).
    :param approved: whether the plan was approved and applied.
    :param approved_plan_hash: plan hash the spec approves, if any.
    :param plan_hash_value: precomputed plan hash; recomputed from steps when
        omitted.
    :param consumed_plan_hash: plan hash already applied, if any.
    :param reconciled_at: timestamp recorded as ``lastReconciled``; defaults to
        now.
    :return: the ``.status`` object for the RedfishNodeProfile CR.
    """
    observed_at = reconciled_at or _utc_now()
    steps = tuple(getattr(result, "steps", ()))
    applied = tuple(getattr(result, "applied", ()))
    planned_steps = [_planned_step(step) for step in steps]
    applied_changes = [_applied_change(change) for change in applied]
    current_plan_hash = plan_hash_value or plan_hash(planned_steps)
    current_consumed_hash = consumed_plan_hash
    if approved and current_plan_hash:
        current_consumed_hash = current_plan_hash
    drift = any(step["required"] for step in planned_steps)
    changed = any(change["changed"] for change in applied_changes)
    if changed:
        applied_reason = "Applied"
    elif approved:
        applied_reason = "NoChanges"
    else:
        applied_reason = "DryRun"
    status = {
        "dryRun": bool(getattr(result, "dry_run", not approved)),
        "drift": drift,
        "plannedSteps": planned_steps,
        "appliedChanges": applied_changes,
        "conditions": [
            _condition(
                "Approved",
                approved,
                _approval_reason(
                    approved=approved,
                    current_plan_hash=current_plan_hash,
                    approved_plan_hash=approved_plan_hash,
                    consumed_plan_hash=consumed_plan_hash,
                ),
                changed_at=observed_at,
            ),
            _condition(
                "DriftDetected",
                drift,
                "PlanRequiresChanges" if drift else "InSync",
                changed_at=observed_at,
            ),
            _condition(
                "Applied",
                bool(changed and approved),
                applied_reason,
                changed_at=observed_at,
            ),
        ],
        "lastReconciled": _rfc3339(observed_at),
    }
    if current_plan_hash:
        status["planHash"] = current_plan_hash
    if current_consumed_hash:
        status["consumedPlanHash"] = current_consumed_hash
    return status


def build_error_status(
    message: str,
    *,
    reconciled_at: datetime | None = None,
) -> dict[str, Any]:
    """Return status for controller failures that occur before reconcile runs.

    :param message: the failure detail recorded on the condition.
    :param reconciled_at: timestamp recorded as ``lastReconciled``; defaults to
        now.
    :return: a ``.status`` object marking reconcile unavailable.
    """
    observed_at = reconciled_at or _utc_now()
    return {
        "dryRun": True,
        "drift": None,
        "plannedSteps": [],
        "appliedChanges": [],
        "conditions": [
            _condition(
                "ReconcileAvailable",
                False,
                "BackendUnavailable",
                message=message,
                changed_at=observed_at,
            )
        ],
        "lastReconciled": _rfc3339(observed_at),
    }


def _endpoint_spec(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the required ``spec.endpoint`` mapping.

    :param spec: the CR spec.
    :return: the ``endpoint`` mapping.
    :raises ValueError: when ``spec.endpoint`` is missing or empty.
    """
    endpoint = _mapping(spec.get("endpoint"))
    if not endpoint:
        raise ValueError("RedfishNodeProfile spec.endpoint is required")
    return endpoint


def _manager_address(endpoint: Mapping[str, Any]) -> tuple[str, int, bool]:
    """Resolve the BMC host, port, and HTTP flag from the endpoint spec.

    Accepts either a bare host/IP (with ``port``) or an ``http(s)://`` URL.

    :param endpoint: the ``spec.endpoint`` mapping (address/port).
    :return: tuple of (host, port, whether to use plain HTTP).
    :raises ValueError: when the address is empty or a URL lacks a host.
    """
    raw_address = str(endpoint.get("address") or "").strip()
    if not raw_address:
        raise ValueError("RedfishNodeProfile spec.endpoint.address is required")

    parsed = urlsplit(raw_address)
    if parsed.scheme in {"http", "https"}:
        host = parsed.hostname or ""
        if not host:
            raise ValueError("RedfishNodeProfile endpoint URL requires a host")
        port = int(parsed.port or endpoint.get("port") or DEFAULT_PORT)
        return host, port, parsed.scheme == "http"

    return raw_address, int(endpoint.get("port") or DEFAULT_PORT), False


def _make_manager(
    endpoint: Mapping[str, Any],
    credentials: Mapping[str, str],
    manager_factory: ManagerFactory,
) -> Any:
    """Build a Redfish manager for the endpoint from spec and credentials.

    :param endpoint: the ``spec.endpoint`` mapping (address/port/insecure).
    :param credentials: username/password mapping for BMC auth.
    :param manager_factory: callable that constructs the manager.
    :return: the constructed manager instance.
    """
    address, port, is_http = _manager_address(endpoint)
    return manager_factory(
        idrac_ip=address,
        idrac_username=credentials.get("username", DEFAULT_USERNAME),
        idrac_password=credentials.get("password", ""),
        idrac_port=port,
        insecure=bool(endpoint.get("insecure", True)),
        is_http=is_http,
        is_debug=False,
    )


def _load_reconcile_func() -> ReconcileFunc:
    """Import and return the reconcile function, lazily.

    :return: the ``redfish_ctl.reconcile.reconcile`` callable.
    :raises RuntimeError: when ``redfish_ctl.reconcile`` cannot be imported.
    """
    try:
        from redfish_ctl.reconcile import reconcile
    except ImportError as exc:  # pragma: no cover - depends on merge order.
        raise RuntimeError("redfish_ctl.reconcile is unavailable") from exc
    return reconcile


def reconcile_profile(
    spec: Mapping[str, Any],
    *,
    credentials: Mapping[str, str] | None = None,
    current_status: Mapping[str, Any] | None = None,
    manager_factory: ManagerFactory = RedfishManagerBase,
    reconcile_func: ReconcileFunc | None = None,
    reconciled_at: datetime | None = None,
) -> dict[str, Any]:
    """Plan or apply the desired state from a RedfishNodeProfile spec.

    Runs a dry-run plan first; applies only when the spec's approved plan hash
    matches the current plan and has not already been consumed.

    :param spec: the CR spec (endpoint, desired state, approval hash).
    :param credentials: username/password mapping for BMC auth.
    :param current_status: the current ``.status``, read for
        ``consumedPlanHash``.
    :param manager_factory: callable that constructs the manager.
    :param reconcile_func: reconcile callable; loaded lazily when omitted.
    :param reconciled_at: timestamp recorded as ``lastReconciled``; defaults to
        now.
    :return: the ``.status`` object (dry-run plan, or applied result when
        approved).
    """
    endpoint = _endpoint_spec(spec)
    desired_state = _mapping(spec.get("desiredState"))
    manager = _make_manager(endpoint, credentials or {}, manager_factory)
    reconcile_action = reconcile_func or _load_reconcile_func()
    dry_run_result = reconcile_action(
        manager,
        desired_state,
        confirm=False,
        wait_for_reboot=False,
        async_call=False,
    )
    dry_status = build_status(
        dry_run_result,
        approved=False,
        approved_plan_hash=str(spec.get("approvedPlanHash") or "") or None,
        consumed_plan_hash=str(_mapping(current_status).get("consumedPlanHash") or "") or None,
        reconciled_at=reconciled_at,
    )
    current_plan_hash = dry_status.get("planHash")
    approved_plan_hash = str(spec.get("approvedPlanHash") or "") or None
    consumed_plan_hash = str(
        _mapping(current_status).get("consumedPlanHash") or ""
    ) or None
    approved = bool(
        current_plan_hash
        and approved_plan_hash == current_plan_hash
        and consumed_plan_hash != current_plan_hash
    )
    if not approved:
        return dry_status

    result = reconcile_action(
        manager,
        desired_state,
        confirm=True,
        wait_for_reboot=bool(spec.get("waitForReboot", False)),
        async_call=False,
    )
    return build_status(
        result,
        approved=approved,
        approved_plan_hash=approved_plan_hash,
        plan_hash_value=current_plan_hash,
        consumed_plan_hash=current_plan_hash,
        reconciled_at=reconciled_at,
    )


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
    """Read credentials from the Secret named by spec.endpoint.secretRef when available.

    Shares the process-wide client from :mod:`redfish_ctl.kube_client` with the
    endpoint controller (both run in one kopf process), so the kube config is
    loaded once for the whole process instead of on every handler thread.

    :param namespace: namespace of the CR (and its Secret); empty skips the read.
    :param secret_ref: ``spec.endpoint.secretRef`` naming the Secret and keys.
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


def reconcile_redfish_node_profile(
    spec: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
    namespace: str | None = None,
    name: str | None = None,
    logger: Any | None = None,
    patch: MutableMapping[str, Any] | None = None,
    **_: Any,
) -> None:
    """Kopf callback that updates only the RedfishNodeProfile status subresource.

    Status is written through the injected ``patch`` object; the handler
    returns ``None`` on purpose. Returning a value makes kopf persist it under
    ``status.reconcile_redfish_node_profile``, a field the structural CRD
    schema rejects, which surfaces a "merge-patching finished with
    inconsistencies" warning on every reconcile.

    :param spec: the CR spec (endpoint, desired state, approval hash).
    :param body: the full CR body; used to read the current ``.status``.
    :param namespace: namespace of the CR, for the Secret read and logging.
    :param name: name of the CR, for logging.
    :param logger: kopf logger; the reconcile is logged when provided.
    :param patch: kopf patch object the new ``.status`` is written into.
    """
    endpoint = _mapping(spec.get("endpoint"))
    with tracing.operation_span("k8s.redfish_node_profile.reconcile") as span:
        _set_controller_span_attributes(
            span,
            endpoint,
            namespace=namespace,
            name=name,
            resource_kind="RedfishNodeProfile",
        )
        credentials = load_secret_credentials(namespace, endpoint.get("secretRef"))
        try:
            status = reconcile_profile(
                spec,
                credentials=credentials,
                current_status=_mapping(_mapping(body).get("status")),
            )
        except Exception as exc:
            tracing.record_exception(span, exc)
            status = build_error_status(str(exc))
        if patch is not None:
            patch.setdefault("status", {}).update(status)
        if logger is not None:
            logger.info("reconciled RedfishNodeProfile %s/%s", namespace or "", name or "")


if kopf is not None:  # pragma: no cover - decorator wiring is runtime-only.
    setup_controller_tracing()
    reconcile_redfish_node_profile = kopf.on.create(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
    )(reconcile_redfish_node_profile)
    reconcile_redfish_node_profile = kopf.on.update(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
    )(reconcile_redfish_node_profile)
    reconcile_redfish_node_profile = kopf.timer(
        REDFISH_GROUP,
        REDFISH_VERSION,
        REDFISH_PLURAL,
        interval=30,
        sharp=True,
    )(reconcile_redfish_node_profile)
