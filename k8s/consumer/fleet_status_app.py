#!/usr/bin/env python3
"""Fleet status consumer for RedfishEndpoint resources.

This is a *consumer* application: it reads the ``.status`` the read-only
RedfishEndpoint controller (``k8s/controller/redfish_endpoint_controller.py``)
writes onto each ``RedfishEndpoint`` custom resource, and presents the fleet as
a live dashboard, a JSON API, and Prometheus metrics.

Layering (one direction only):

    BMC  --(read-only poll)-->  controller  --writes .status-->  RedfishEndpoint CR
                                                                        |
                                                          (read .status only)
                                                                        v
                                                              this consumer app

The consumer NEVER talks to a BMC and NEVER reads a Secret. Its Kubernetes RBAC
is get/list/watch on ``redfishendpoints`` only, so a compromise here cannot
reach a BMC credential or mutate anything.

The pure rendering helpers (``normalize_endpoint``, ``fleet_summary``,
``render_fleet_json``, ``render_metrics``, ``render_html``) carry no Kubernetes
dependency so they are unit-testable offline; the Kubernetes client is imported
lazily inside ``load_endpoints`` (guarded like the controller guards ``kopf``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

REDFISH_GROUP = "redfish.ctl.dev"
REDFISH_VERSION = "v1alpha1"
REDFISH_PLURAL = "redfishendpoints"

# Health ranking so the fleet summary can pick the worst state deterministically.
_HEALTH_RANK = {"OK": 0, "Warning": 1, "Critical": 2}
_POWER_ON_STATES = {"On", "PoweringOn"}


def _as_number(value: Any) -> float | None:
    """Coerce a Redfish reading to float, tolerating strings and rejecting bools."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce a count to int with the same string-tolerant, bool-rejecting rules."""
    number = _as_number(value)
    return int(number) if number is not None else None


def _epoch_from_rfc3339(value: str | None) -> float | None:
    """Parse the controller's RFC3339 ``lastPolled`` into an epoch second."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _infer_backend(address: str) -> str:
    """Label an endpoint as an in-cluster mock or a live BMC by its address.

    A ``*.svc.cluster.local`` address is served by the sandbox mock-BMC; anything
    else is treated as a real/live target. Purely cosmetic (dashboard badge).
    """
    host = urlsplit(address).netloc or address
    if host.endswith(".svc.cluster.local") or host in {"mock-bmc", "ilo-sim"}:
        return "mock"
    return "live"


def normalize_endpoint(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one RedfishEndpoint object into a stable consumer-facing row.

    Tolerates a resource the controller has not polled yet: an absent or empty
    ``status`` yields ``state="Pending"`` with null readings rather than raising.
    """
    meta = obj.get("metadata") or {}
    spec = obj.get("spec") or {}
    status = obj.get("status") or {}
    temperature = status.get("temperature") or {}
    network_firmware = status.get("networkFirmware") or {}

    address = str(spec.get("address") or "")
    power_state = status.get("powerState")
    health = status.get("health")
    last_polled = status.get("lastPolled")

    if not status or (power_state is None and health is None and not last_polled):
        state = "Pending"
    else:
        state = "Ready"

    nic_components = [
        {
            "id": comp.get("id"),
            "deviceClass": comp.get("deviceClass"),
            "version": comp.get("version"),
            "updateable": comp.get("updateable"),
        }
        for comp in (network_firmware.get("components") or [])
        if isinstance(comp, Mapping)
    ]
    nic_versions = [
        str(v) for v in (network_firmware.get("distinctVersions") or []) if v is not None
    ]

    return {
        "name": str(meta.get("name") or ""),
        "namespace": str(meta.get("namespace") or ""),
        "address": address,
        "port": spec.get("port"),
        "insecure": bool(spec.get("insecure", True)),
        "backend": _infer_backend(address),
        "state": state,
        "powerState": power_state,
        "health": health,
        "temperatureCount": _as_int(temperature.get("count")),
        "temperatureMaxCelsius": _as_number(temperature.get("maxCelsius")),
        "nicAdapterCount": _as_int(network_firmware.get("adapterCount")),
        "nicCount": _as_int(network_firmware.get("nicCount")),
        "dpuCount": _as_int(network_firmware.get("dpuCount")),
        "nicFirmwareCount": _as_int(network_firmware.get("firmwareCount")),
        "nicFirmwareUpdateableCount": _as_int(network_firmware.get("updateableCount")),
        "nicFirmwareVersions": nic_versions,
        "nicFirmware": nic_components,
        "lastPolled": last_polled,
    }


def fleet_summary(nodes: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Aggregate per-node rows into fleet counters for the dashboard header."""
    summary = {
        "total": len(nodes),
        "ready": 0,
        "pending": 0,
        "poweredOn": 0,
        "poweredOff": 0,
        "healthy": 0,
        "warning": 0,
        "critical": 0,
    }
    for node in nodes:
        if node.get("state") == "Ready":
            summary["ready"] += 1
        else:
            summary["pending"] += 1
        power = node.get("powerState")
        if power in _POWER_ON_STATES:
            summary["poweredOn"] += 1
        elif power is not None:
            summary["poweredOff"] += 1
        health = node.get("health")
        if health == "OK":
            summary["healthy"] += 1
        elif health == "Warning":
            summary["warning"] += 1
        elif health == "Critical":
            summary["critical"] += 1
    return summary


def fleet_firmware_drift(nodes: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Map firmware component id -> sorted versions when nodes DISAGREE on it.

    Drift is a cross-node property: a single node legitimately runs different
    versions for different components (e.g. a ConnectX NIC at 40.x and its
    BlueField DPU at 32.x), which is NOT drift. Real drift is the same component
    id (``CX8_0``) carrying different versions across the fleet. An empty result
    means every node runs the same firmware per component.
    """
    by_component: dict[str, set[str]] = {}
    for node in nodes:
        for comp in node.get("nicFirmware") or []:
            if not isinstance(comp, Mapping):
                continue
            cid, ver = comp.get("id"), comp.get("version")
            if cid is None or ver is None:
                continue
            by_component.setdefault(str(cid), set()).add(str(ver))
    return {cid: sorted(vers) for cid, vers in by_component.items() if len(vers) > 1}


def render_fleet_json(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Assemble the ``/api/nodes`` payload: summary + normalized node rows."""
    node_list = list(nodes)
    summary = fleet_summary(node_list)
    summary["firmwareDriftComponents"] = fleet_firmware_drift(node_list)
    return {
        "summary": summary,
        "nodes": node_list,
    }


def find_node(
    nodes: Sequence[Mapping[str, Any]], name: str
) -> Mapping[str, Any] | None:
    """Return the node row with matching name, or None (backs /api/nodes/<name>)."""
    return next((node for node in nodes if node.get("name") == name), None)


def _escape_label(value: Any) -> str:
    """Escape a Prometheus label value per the text exposition spec.

    Backslash, double-quote, and line-feed/carriage-return must be escaped, or a
    value containing one (e.g. a newline in an operator-supplied address) would
    split or corrupt the metric line and fail the whole scrape.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _metric_line(name: str, labels: Mapping[str, str], value: float | int) -> str:
    label_str = ",".join(f'{key}="{_escape_label(val)}"' for key, val in labels.items())
    return f"{name}{{{label_str}}} {value}"


def render_metrics(nodes: Sequence[Mapping[str, Any]]) -> str:
    """Render the fleet as Prometheus text exposition.

    Exposes per-node gauges keyed by ``node`` so an operator can alert on power
    loss, health degradation, or a temperature ceiling straight off the CR
    status the controller populated — no direct BMC scrape needed.
    """
    lines: list[str] = []
    lines.append("# HELP redfish_endpoint_info Static endpoint metadata (value always 1).")
    lines.append("# TYPE redfish_endpoint_info gauge")
    lines.append("# HELP redfish_endpoint_power_on 1 when the BMC reports a powered-on state.")
    lines.append("# TYPE redfish_endpoint_power_on gauge")
    lines.append("# HELP redfish_endpoint_health Health rank: 0 OK, 1 Warning, 2 Critical, -1 unknown.")
    lines.append("# TYPE redfish_endpoint_health gauge")
    lines.append("# HELP redfish_endpoint_temperature_max_celsius Hottest reported sensor in Celsius.")
    lines.append("# TYPE redfish_endpoint_temperature_max_celsius gauge")
    lines.append("# HELP redfish_endpoint_temperature_sensor_count Number of temperature sensors read.")
    lines.append("# TYPE redfish_endpoint_temperature_sensor_count gauge")
    lines.append("# HELP redfish_endpoint_last_polled_timestamp_seconds Unix time of the last controller poll.")
    lines.append("# TYPE redfish_endpoint_last_polled_timestamp_seconds gauge")
    lines.append("# HELP redfish_nic_firmware_info One NIC/DPU firmware component (value always 1).")
    lines.append("# TYPE redfish_nic_firmware_info gauge")
    lines.append("# HELP redfish_node_nic_count Number of NIC/DPU adapters on the node.")
    lines.append("# TYPE redfish_node_nic_count gauge")
    lines.append("# HELP redfish_node_nic_firmware_distinct_versions Distinct firmware versions on the node (a NIC + its DPU differ normally; this is not drift).")
    lines.append("# TYPE redfish_node_nic_firmware_distinct_versions gauge")
    lines.append("# HELP redfish_fleet_nic_firmware_drift_components Firmware components whose version differs ACROSS nodes (real fleet drift; 0 is healthy).")
    lines.append("# TYPE redfish_fleet_nic_firmware_drift_components gauge")

    drift = fleet_firmware_drift(nodes)
    lines.append(_metric_line("redfish_fleet_nic_firmware_drift_components", {}, len(drift)))

    for node in nodes:
        name = str(node.get("name") or "")
        info_labels = {
            "node": name,
            "address": str(node.get("address") or ""),
            "backend": str(node.get("backend") or ""),
            "power_state": str(node.get("powerState") or "unknown"),
            "health": str(node.get("health") or "unknown"),
            "state": str(node.get("state") or ""),
        }
        lines.append(_metric_line("redfish_endpoint_info", info_labels, 1))

        power = node.get("powerState")
        lines.append(
            _metric_line(
                "redfish_endpoint_power_on", {"node": name}, 1 if power in _POWER_ON_STATES else 0
            )
        )

        health_rank = _HEALTH_RANK.get(str(node.get("health")), -1)
        lines.append(_metric_line("redfish_endpoint_health", {"node": name}, health_rank))

        temp_max = node.get("temperatureMaxCelsius")
        if temp_max is not None:
            lines.append(
                _metric_line("redfish_endpoint_temperature_max_celsius", {"node": name}, temp_max)
            )

        count = node.get("temperatureCount")
        if isinstance(count, int):
            lines.append(
                _metric_line("redfish_endpoint_temperature_sensor_count", {"node": name}, count)
            )

        polled = _epoch_from_rfc3339(node.get("lastPolled"))
        if polled is not None:
            lines.append(
                _metric_line(
                    "redfish_endpoint_last_polled_timestamp_seconds", {"node": name}, int(polled)
                )
            )

        nic_count = node.get("nicAdapterCount")
        if isinstance(nic_count, int):
            lines.append(_metric_line("redfish_node_nic_count", {"node": name}, nic_count))

        versions = node.get("nicFirmwareVersions") or []
        lines.append(
            _metric_line(
                "redfish_node_nic_firmware_distinct_versions", {"node": name}, len(versions)
            )
        )

        for comp in node.get("nicFirmware") or []:
            if not isinstance(comp, Mapping):
                continue
            fw_labels = {
                "node": name,
                "nic_id": str(comp.get("id") or ""),
                "device_class": str(comp.get("deviceClass") or ""),
                "version": str(comp.get("version") or "unknown"),
            }
            lines.append(_metric_line("redfish_nic_firmware_info", fw_labels, 1))

    return "\n".join(lines) + "\n"


def _esc(value: Any) -> str:
    """Minimal HTML-escape for text interpolated into the dashboard."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _nic_firmware_cell(node: Mapping[str, Any], drift_ids: set[str]) -> str:
    """Render the NIC-firmware table cell: version(s) + adapter count.

    ``drift_ids`` are firmware component ids that disagree across the fleet; the
    cell is drift-flagged only if this node carries such a component — so a lone
    node with a NIC and a DPU (two versions, but no cross-node disagreement) is
    not falsely flagged.
    """
    versions = node.get("nicFirmwareVersions") or []
    adapters = node.get("nicAdapterCount")
    if not versions:
        return '<td class="nicfw">—</td>'
    ver_txt = ", ".join(_esc(v) for v in versions)
    cards = f' · {adapters} card{"s" if adapters != 1 else ""}' if isinstance(adapters, int) else ""
    node_drifts = any(
        isinstance(c, Mapping) and str(c.get("id")) in drift_ids
        for c in (node.get("nicFirmware") or [])
    )
    drift = " drift" if node_drifts else ""
    return f'<td class="nicfw"><span class="fw{drift}">{ver_txt}</span>{cards}</td>'


def render_html(nodes: Sequence[Mapping[str, Any]], generated_at: str | None = None) -> str:
    """Render the live fleet dashboard.

    Server-rendered so it works with zero external assets (CSP-safe, offline); a
    tiny inline poller refetches ``/api/nodes`` to keep the tab title current, and
    a 10s interval reloads the page so the table tracks the controller's polls.
    """
    summary = fleet_summary(nodes)
    drift_map = fleet_firmware_drift(nodes)
    drift_ids = set(drift_map)
    drift_components = len(drift_map)
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    rows = []
    for node in nodes:
        health = node.get("health") or "—"
        power_state = node.get("powerState")
        power = power_state or "—"
        health_cls = {
            "OK": "ok",
            "Warning": "warn",
            "Critical": "crit",
        }.get(str(node.get("health")), "unknown")
        if power_state in _POWER_ON_STATES:
            power_cls = "on"
        elif power_state is None:
            power_cls = "unknown"
        else:
            power_cls = "off"
        temp = node.get("temperatureMaxCelsius")
        temp_txt = f"{temp:.1f} °C" if isinstance(temp, (int, float)) else "—"
        count = node.get("temperatureCount")
        count_txt = str(count) if isinstance(count, int) else "—"
        backend = node.get("backend") or ""
        rows.append(
            "<tr>"
            f'<td class="name">{_esc(node.get("name"))}</td>'
            f'<td><span class="badge {"" if backend == "live" else "mock"}">{_esc(backend)}</span></td>'
            f'<td class="addr">{_esc(node.get("address"))}</td>'
            f'<td><span class="pill {power_cls}">{_esc(power)}</span></td>'
            f'<td><span class="pill {health_cls}">{_esc(health)}</span></td>'
            f"<td>{_esc(temp_txt)}</td>"
            f"<td>{_esc(count_txt)}</td>"
            f"{_nic_firmware_cell(node, drift_ids)}"
            f'<td class="stamp">{_esc(node.get("lastPolled") or "—")}</td>'
            f'<td><span class="state {_esc(str(node.get("state")).lower())}">{_esc(node.get("state"))}</span></td>'
            "</tr>"
        )
    rows_html = "\n".join(rows) or (
        '<tr><td colspan="10" class="empty">No RedfishEndpoint resources found in this namespace.</td></tr>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redfish Fleet Status</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
    background: #0f1115; color: #e6e6e6; }}
  header {{ padding: 20px 28px; border-bottom: 1px solid #262a33;
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
  h1 {{ font-size: 18px; margin: 0; font-weight: 600; }}
  .sub {{ color: #8b93a7; font-size: 13px; }}
  .cards {{ display: flex; gap: 12px; padding: 18px 28px; flex-wrap: wrap; }}
  .card {{ background: #171a21; border: 1px solid #262a33; border-radius: 10px;
    padding: 12px 16px; min-width: 96px; }}
  .card .n {{ font-size: 24px; font-weight: 700; }}
  .card .l {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
    color: #8b93a7; }}
  table {{ width: calc(100% - 56px); margin: 8px 28px 32px; border-collapse: collapse;
    background: #171a21; border: 1px solid #262a33; border-radius: 10px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 14px; font-size: 13px;
    border-bottom: 1px solid #21252e; }}
  th {{ color: #8b93a7; font-weight: 600; text-transform: uppercase; font-size: 11px;
    letter-spacing: .05em; }}
  td.name {{ font-weight: 600; }}
  td.addr, td.stamp {{ color: #8b93a7; font-family: ui-monospace, monospace; font-size: 12px; }}
  .pill {{ padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .pill.on {{ background: #12351f; color: #4ade80; }}
  .pill.off {{ background: #3a1f24; color: #f87171; }}
  .pill.ok {{ background: #12351f; color: #4ade80; }}
  .pill.warn {{ background: #3a2f12; color: #fbbf24; }}
  .pill.crit {{ background: #3a1f24; color: #f87171; }}
  .pill.unknown {{ background: #262a33; color: #8b93a7; }}
  .badge {{ padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600;
    background: #1c2b3a; color: #60a5fa; text-transform: uppercase; }}
  .badge.mock {{ background: #2b2436; color: #c084fc; }}
  .state.ready {{ color: #4ade80; }}
  .state.pending {{ color: #fbbf24; }}
  td.nicfw {{ color: #8b93a7; font-size: 12px; }}
  td.nicfw .fw {{ font-family: ui-monospace, monospace; color: #c9d1d9; }}
  td.nicfw .fw.drift {{ color: #fbbf24; font-weight: 600; }}
  .card.alert .n {{ color: #fbbf24; }}
  td.empty {{ text-align: center; color: #8b93a7; padding: 28px; }}
  a {{ color: #60a5fa; }}
</style>
</head>
<body>
<header>
  <h1>Redfish Fleet Status</h1>
  <span class="sub">consumer reads <code>RedfishEndpoint.status</code> written by the controller
    &middot; <a href="/api/nodes">/api/nodes</a> &middot; <a href="/metrics">/metrics</a>
    &middot; updated <span id="stamp">{_esc(stamp)}</span></span>
</header>
<div class="cards">
  <div class="card"><div class="n">{summary["total"]}</div><div class="l">Endpoints</div></div>
  <div class="card"><div class="n">{summary["poweredOn"]}</div><div class="l">Powered On</div></div>
  <div class="card"><div class="n">{summary["healthy"]}</div><div class="l">Healthy</div></div>
  <div class="card"><div class="n">{summary["warning"] + summary["critical"]}</div><div class="l">Degraded</div></div>
  <div class="card"><div class="n">{summary["pending"]}</div><div class="l">Pending</div></div>
  <div class="card{" alert" if drift_components else ""}" title="Firmware components whose version differs across the fleet"><div class="n">{drift_components}</div><div class="l">NIC FW Drift</div></div>
</div>
<table>
  <thead><tr>
    <th>Node</th><th>Backend</th><th>Address</th><th>Power</th><th>Health</th>
    <th>Max Temp</th><th>Sensors</th><th>NIC Firmware</th><th>Last Polled</th><th>State</th>
  </tr></thead>
  <tbody id="rows">
{rows_html}
  </tbody>
</table>
<script>
  // Track the controller's next poll without a full reload.
  async function refresh() {{
    try {{
      const r = await fetch('/api/nodes', {{cache: 'no-store'}});
      if (!r.ok) return;
      const data = await r.json();
      document.title = `Redfish Fleet (${{data.summary.total}})`;
    }} catch (e) {{ /* transient; keep the last render */ }}
  }}
  setInterval(() => location.reload(), 10000);
  refresh();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Kubernetes-facing layer (lazy import; not exercised by offline unit tests).
# --------------------------------------------------------------------------- #


class _EndpointCache:
    """Short-TTL cache over the k8s API so many dashboard clients don't fan out
    into an API-server request per hit (the avoid-round-trips principle applied
    to the control plane)."""

    def __init__(self, ttl_seconds: float = 3.0) -> None:
        self._ttl = ttl_seconds
        self._cond = threading.Condition()
        self._at = 0.0
        self._value: list[dict[str, Any]] = []
        self._loading = False

    def _is_fresh(self) -> bool:
        # Callers hold ``self._cond``. ``_at == 0`` means never loaded.
        return bool(self._at) and (time.monotonic() - self._at) <= self._ttl

    def get(self, loader) -> list[dict[str, Any]]:
        with self._cond:
            if self._is_fresh():
                return self._value
            # Single-flight: the first caller past a stale TTL performs the load;
            # concurrent callers wait on the condition and reuse its result, so a
            # burst of dashboard clients triggers at most one k8s API refill per
            # TTL instead of a thundering herd of parallel list calls.
            while self._loading:
                self._cond.wait()
                if self._is_fresh():
                    return self._value
            self._loading = True
        try:
            fresh = loader()
        except BaseException:
            # Release the in-flight flag so a failed load doesn't wedge the cache;
            # the next waiter retries.
            with self._cond:
                self._loading = False
                self._cond.notify_all()
            raise
        with self._cond:
            self._value = fresh
            self._at = time.monotonic()
            self._loading = False
            self._cond.notify_all()
        return fresh


def load_endpoints(namespace: str) -> list[dict[str, Any]]:  # pragma: no cover - needs cluster
    """List RedfishEndpoint CRs in ``namespace`` and normalize them.

    Imports the Kubernetes client lazily so the pure renderers above stay
    importable (and unit-testable) without the ``kubernetes`` package.
    """
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    resp = api.list_namespaced_custom_object(
        group=REDFISH_GROUP,
        version=REDFISH_VERSION,
        namespace=namespace,
        plural=REDFISH_PLURAL,
    )
    items = resp.get("items", []) if isinstance(resp, Mapping) else []
    nodes = [normalize_endpoint(item) for item in items]
    nodes.sort(key=lambda n: n["name"])
    return nodes


class _Handler(BaseHTTPRequestHandler):
    server_version = "redfish-fleet-consumer/1.0"
    namespace = "redfish-sandbox"
    cache: _EndpointCache = _EndpointCache()

    def log_message(self, *_args: Any) -> None:  # keep stdout to real events
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _nodes(self) -> list[dict[str, Any]]:
        return self.cache.get(lambda: load_endpoints(self.namespace))

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if path == "/healthz":
                self._send(200, b'{"status":"ok"}', "application/json")
                return
            if path == "/":
                body = render_html(self._nodes()).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path == "/api/nodes":
                body = json.dumps(render_fleet_json(self._nodes())).encode("utf-8")
                self._send(200, body, "application/json")
                return
            if path.startswith("/api/nodes/"):
                wanted = path[len("/api/nodes/") :]
                match = find_node(self._nodes(), wanted)
                if match is None:
                    self._send(404, b'{"error":"not found"}', "application/json")
                    return
                self._send(200, json.dumps(match).encode("utf-8"), "application/json")
                return
            if path == "/metrics":
                body = render_metrics(self._nodes()).encode("utf-8")
                self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
                return
            self._send(404, b'{"error":"not found"}', "application/json")
        except Exception as exc:  # surface a 503 rather than a bare stack trace
            msg = json.dumps({"error": "backend unavailable", "detail": str(exc)}).encode("utf-8")
            self._send(503, msg, "application/json")

    do_HEAD = do_GET


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    namespace: str | None = None,
) -> None:  # pragma: no cover - process entry point
    """Serve the dashboard/API/metrics with a thread-per-request server."""
    _Handler.namespace = namespace or os.environ.get("WATCH_NAMESPACE", "redfish-sandbox")
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        f"redfish-fleet-consumer serving on {host}:{port} "
        f"(namespace={_Handler.namespace})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    run_server(
        host=os.environ.get("BIND_HOST", "0.0.0.0"),
        port=int(os.environ.get("BIND_PORT", "8080")),
        namespace=os.environ.get("WATCH_NAMESPACE"),
    )
