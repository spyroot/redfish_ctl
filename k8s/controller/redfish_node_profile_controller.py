#!/usr/bin/env python3
"""Desired-state controller for RedfishNodeProfile resources."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

try:  # pragma: no cover - exercised only in a deployed controller image.
    import kopf
except ImportError:  # pragma: no cover - unit tests call the handler directly.
    kopf = None

from redfish_ctl.redfish_manager_base import RedfishManagerBase

REDFISH_GROUP = "redfish.ctl.dev"
REDFISH_VERSION = "v1alpha1"
REDFISH_PLURAL = "redfishnodeprofiles"
DEFAULT_PORT = 443
DEFAULT_USERNAME = "root"

ManagerFactory = Callable[..., Any]
ReconcileFunc = Callable[..., Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _condition(
    condition_type: str,
    status: bool | None,
    reason: str,
    *,
    message: str = "",
    changed_at: datetime,
) -> dict[str, str]:
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
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _planned_step(step: Any) -> dict[str, Any]:
    return {
        "kind": str(getattr(step, "kind", "")),
        "required": bool(getattr(step, "required", False)),
        "description": str(getattr(step, "description", "")),
        "preview": _as_dict(getattr(step, "preview", {})),
    }


def _applied_change(change: Any) -> dict[str, Any]:
    return {
        "kind": str(getattr(change, "kind", "")),
        "changed": bool(getattr(change, "changed", False)),
        "result": _as_dict(getattr(change, "result", {})),
    }


def plan_hash(planned_steps: list[dict[str, Any]]) -> str | None:
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
    """Return the RedfishNodeProfile status object from a reconcile result."""
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
    """Return status for controller failures that occur before reconcile runs."""
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
    endpoint = _mapping(spec.get("endpoint"))
    if not endpoint:
        raise ValueError("RedfishNodeProfile spec.endpoint is required")
    return endpoint


def _manager_address(endpoint: Mapping[str, Any]) -> tuple[str, int, bool]:
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
    """Plan or apply the desired state from a RedfishNodeProfile spec."""
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
    encoded = data.get(key)
    if not encoded:
        return None
    return base64.b64decode(encoded).decode("utf-8")


def load_secret_credentials(
    namespace: str | None,
    secret_ref: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Read credentials from the Secret named by spec.endpoint.secretRef when available."""
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
    """
    endpoint = _mapping(spec.get("endpoint"))
    credentials = load_secret_credentials(namespace, endpoint.get("secretRef"))
    try:
        status = reconcile_profile(
            spec,
            credentials=credentials,
            current_status=_mapping(_mapping(body).get("status")),
        )
    except Exception as exc:
        status = build_error_status(str(exc))
    if patch is not None:
        patch.setdefault("status", {}).update(status)
    if logger is not None:
        logger.info("reconciled RedfishNodeProfile %s/%s", namespace or "", name or "")


if kopf is not None:  # pragma: no cover - decorator wiring is runtime-only.
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
