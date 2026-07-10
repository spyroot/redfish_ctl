"""Map Redfish telemetry rows into Prometheus and SignalFx metrics."""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

REQUIRED_DIMENSIONS = ("host.name", "node", "server.address", "bmc.ip", "vendor")
SENSOR_METRIC = {
    "Temperature": ("hw.temperature", "sensor"),
    "Rotational": ("hw.fan_speed", "fan"),
    "Voltage": ("hw.voltage", "sensor"),
}
FABRIC_PROPERTY_METRICS = {
    "BitErrorRate": "hw.fabric.bit_error_rate",
    "CurrentSpeedGbps": "hw.fabric.port_speed",
    "CRCErrorCount": "hw.fabric.crc_errors",
    "EffectiveBER": "hw.fabric.effective_ber",
    "EffectiveError": "hw.fabric.effective_errors",
    "FECErrorCount": "hw.fabric.fec_errors",
    "IntentionalLinkDownCount": "hw.fabric.intentional_link_down_count",
    "LinkDownedCount": "hw.fabric.link_down_count",
    "LinkErrorRecoveryCount": "hw.fabric.link_error_recovery_count",
    "MalformedPackets": "hw.fabric.malformed_packets",
    "NVLinkDataRxBandwidthGbps": "hw.fabric.rx_gbps",
    "NVLinkDataTxBandwidthGbps": "hw.fabric.tx_gbps",
    "NVLinkRawRxBandwidthGbps": "hw.fabric.raw_rx_gbps",
    "NVLinkRawTxBandwidthGbps": "hw.fabric.raw_tx_gbps",
    "RXBytes": "hw.fabric.rx_bytes",
    "RXErrors": "hw.fabric.rx_errors",
    "RXFrames": "hw.fabric.rx_frames",
    "RXNoProtocolBytes": "hw.fabric.rx_no_protocol_bytes",
    "RXRemotePhysicalErrors": "hw.fabric.rx_remote_physical_errors",
    "RXSwitchRelayErrors": "hw.fabric.rx_switch_relay_errors",
    "SymbolErrors": "hw.fabric.symbol_errors",
    "TXBytes": "hw.fabric.tx_bytes",
    "TXDiscards": "hw.fabric.tx_discards",
    "TXFrames": "hw.fabric.tx_frames",
    "TXNoProtocolBytes": "hw.fabric.tx_no_protocol_bytes",
    "TXWait": "hw.fabric.tx_wait",
    "TotalRawBER": "hw.fabric.raw_ber",
    "TotalRawError": "hw.fabric.raw_errors",
    "UnintentionalLinkDownCount": "hw.fabric.unintentional_link_down_count",
    "VL15Dropped": "hw.fabric.vl15_dropped",
    "VL15TXBytes": "hw.fabric.vl15_tx_bytes",
    "VL15TXPackets": "hw.fabric.vl15_tx_packets",
}
SECRET_ARG_NAMES = {"--idrac_password", "--idrac-password"}
DIM_VALUE_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
# push_signalfx POSTs the ingest URL as-is, so it must be the full SignalFx
# datapoint endpoint (…/v2/datapoint), never a bare host.
SIGNALFX_DATAPOINT_PATH = "/v2/datapoint"


@dataclass(frozen=True)
class MetricSample:
    """One vendor-neutral telemetry sample ready for export."""

    metric: str
    value: float
    dimensions: Mapping[str, str]
    metric_type: str = "gauge"
    unit: Optional[str] = None
    timestamp: Optional[str] = None


def build_identity_dimensions(
        bmc_ip: str,
        vendor: str = "unknown",
        host_prefix: str = "gb300-poc1",
        bmc_octet_base: int = 20,
        server_octet_base: int = 40,
        server_subnet: Optional[str] = None) -> dict[str, str]:
    """Return the fixed join dimensions required on every exported series."""
    bmc = str(bmc_ip or "unknown")
    parts = bmc.split(".")
    if len(parts) == 4 and parts[-1].isdigit():
        slot = int(parts[-1]) - bmc_octet_base
        subnet = server_subnet or ".".join(parts[:3])
        node = f"slot{slot}"
        host = f"{host_prefix}-{node}"
        server = f"{subnet}.{server_octet_base + slot}"
    else:
        node = "unknown"
        host = bmc
        server = "unknown"
    return {
        "host.name": host,
        "node": node,
        "server.address": server,
        "bmc.ip": bmc,
        "vendor": str(vendor or "unknown").lower(),
    }


# Credential-file keys the exporter honors. REDFISH_* is the going-forward set;
# the legacy IDRAC_* keys still work as a fallback during the rename.
_EXPORTER_CRED_KEYS = frozenset({
    "REDFISH_IP", "REDFISH_USERNAME", "REDFISH_PASSWORD", "REDFISH_PORT",
    "IDRAC_IP", "IDRAC_USERNAME", "IDRAC_PASSWORD", "IDRAC_PORT",
})


def load_exporter_env_file(path: os.PathLike[str] | str) -> dict[str, str]:
    """Read a simple KEY=VALUE runtime env file without printing secret values.

    Accepts REDFISH_IP/USERNAME/PASSWORD/PORT and the legacy IDRAC_* names.
    """
    values = {}
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in _EXPORTER_CRED_KEYS:
            values[key] = value.strip().strip("'\"")
    return values


def exporter_argv_uses_secret(argv: Iterable[str]) -> bool:
    """True when the exporter invocation carries a password on argv."""
    args = list(argv)
    if "exporter" not in args:
        return False
    for arg in args:
        if any(arg == name or arg.startswith(f"{name}=") for name in SECRET_ARG_NAMES):
            return True
    return False


def apply_exporter_env_file(args, path: Optional[str] = None) -> None:
    """Apply exporter credential-file values to an argparse namespace in place."""
    file_path = path or getattr(args, "exporter_credential_file", None)
    file_path = (file_path
                 or os.environ.get("REDFISH_EXPORTER_CREDENTIAL_FILE")
                 or os.environ.get("IDRAC_EXPORTER_CREDENTIAL_FILE"))
    if not file_path:
        return
    values = load_exporter_env_file(file_path)
    # (namespace attr, REDFISH_* key, legacy IDRAC_* key). REDFISH_* wins when both
    # are present in the file; IDRAC_* is the fallback.
    mapping = (
        ("idrac_ip", "REDFISH_IP", "IDRAC_IP"),
        ("idrac_username", "REDFISH_USERNAME", "IDRAC_USERNAME"),
        ("idrac_password", "REDFISH_PASSWORD", "IDRAC_PASSWORD"),
        ("idrac_port", "REDFISH_PORT", "IDRAC_PORT"),
    )
    for attr, redfish_key, idrac_key in mapping:
        key = redfish_key if redfish_key in values else idrac_key
        if key not in values:
            continue
        is_password = attr == "idrac_password"
        current = getattr(args, attr, "")
        if current in ("", None, "root") or is_password:
            value = values[key]
            setattr(args, attr, int(value) if attr == "idrac_port" else value)


def build_metric_samples(
        identity: Mapping[str, str],
        environment_rows: Iterable[Mapping],
        sensor_rows: Iterable[Mapping],
        nvlink_rows: Iterable[Mapping],
        metric_report_rows: Iterable[Mapping],
        network_rows: Iterable[Mapping] = (),
        component_integrity_rows: Iterable[Mapping] = ()) -> list[MetricSample]:
    """Build exporter samples from normalized Redfish command rows."""
    samples: list[MetricSample] = []
    samples.extend(samples_from_environment_rows(environment_rows, identity))
    samples.extend(samples_from_sensor_rows(sensor_rows, identity))
    samples.extend(samples_from_nvlink_rows(nvlink_rows, identity))
    samples.extend(samples_from_metric_report_rows(metric_report_rows, identity))
    samples.extend(samples_from_network_rows(network_rows, identity))
    samples.extend(samples_from_component_integrity_rows(component_integrity_rows, identity))
    return samples


def samples_from_environment_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map Chassis EnvironmentMetrics rows into chassis/GPU power metrics."""
    samples = []
    for row in rows:
        chassis = _environment_chassis(row)
        dims = _environment_dims(identity, row, chassis)
        gpu = _environment_gpu(row, chassis)
        power = _as_float(_reading(row.get("PowerWatts")))
        if power is not None:
            metric = "hw.gpu.power" if gpu and row.get("ParentType") != "Memory" else "hw.power"
            samples.append(_sample(metric, power, dims | ({"gpu": gpu} if gpu else {}), unit="W"))
        energy = _as_float(_reading(row.get("EnergykWh") or row.get("EnergyKWh")))
        if energy is not None:
            samples.append(_sample(
                "hw.energy_kwh",
                energy,
                dims | ({"gpu": gpu} if gpu else {}),
                unit="kWh",
            ))
        for fan_name, rpm in _fan_readings(row):
            samples.append(_sample("hw.fan_speed", rpm, dims | {"fan": _dim_value(fan_name)}, "RPM"))
    return samples


def samples_from_sensor_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map Redfish Sensor rows into chassis thermal/fan/voltage/GPU power metrics."""
    samples = []
    for row in rows:
        value = _as_float(row.get("Reading"))
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        reading_type = row.get("ReadingType")
        name = str(row.get("Name") or "sensor")
        dims = _with_dims(identity, source="sensor", chassis=chassis)
        health = row.get("Health")
        if health:
            dims["health"] = str(health)
        if reading_type == "Power" and _gpu_from_chassis(chassis):
            samples.append(_sample("hw.gpu.power", value, dims | _gpu_dim(chassis), "W"))
        elif reading_type == "Power":
            samples.append(_sample("hw.power", value, dims | {"sensor": _dim_value(name)}, "W"))
        elif reading_type in SENSOR_METRIC:
            metric, label = SENSOR_METRIC[reading_type]
            samples.append(_sample(metric, value, dims | {label: _dim_value(name)}, row.get("ReadingUnits")))
    return samples


def samples_from_nvlink_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map nvlink-ports rows into per-link fabric metrics."""
    samples = []
    for row in rows:
        dims = _fabric_dims(identity, row.get("System"), row.get("GPU"), row.get("Port"), "nvlink")
        link_up = 1.0 if row.get("LinkStatus") == "LinkUp" else 0.0
        samples.append(_sample("hw.fabric.link_up", link_up, dims, None))
        for key, metric, unit in (
                ("CurrentSpeedGbps", "hw.fabric.port_speed", "Gbps"),
                ("RXBytes", "hw.fabric.rx_bytes", "By"),
                ("TXBytes", "hw.fabric.tx_bytes", "By"),
                ("BitErrorRate", "hw.fabric.bit_error_rate", None)):
            value = _as_float(row.get(key))
            if value is not None:
                samples.append(_sample(metric, value, dims, unit))
    return samples


def samples_from_metric_report_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map every TelemetryService MetricReport row into a metric sample.

    Fabric properties get curated ``hw.fabric.*`` names and fabric dimensions;
    every other property (GPU FP16/FP32 activity, thermal, power, memory, …) is
    emitted under a generic ``hw.gb300.*`` name so the FULL telemetry surface
    reaches OTel/Prometheus, not just the fabric subset.
    """
    samples = []
    for row in rows:
        prop = row.get("MetricProperty")
        if not prop:
            continue
        prop_info = _parse_metric_property(str(prop))
        value = _as_float(row.get("MetricValue"))
        if value is None:
            continue
        prop_name = prop_info["property"]
        if prop_name in FABRIC_PROPERTY_METRICS:
            metric = FABRIC_PROPERTY_METRICS[prop_name]
            fabric = "ib" if prop_info.get("port", "").lower().startswith("ib") else "nvlink"
            dims = _fabric_dims(identity, prop_info.get("system"),
                                prop_info.get("gpu"), prop_info.get("port"), fabric)
        else:
            metric = _generic_metric_name(prop_name)
            dims = _with_dims(identity, source="metric-report",
                              property=_dim_value(prop_name))
            for key in ("system", "gpu", "port", "chassis", "index"):
                if prop_info.get(key):
                    dims[key] = str(prop_info[key])
        dims["report"] = str(row.get("Report") or "unknown")
        samples.append(_sample(metric, value, dims, _unit_for_metric(metric), row.get("Timestamp")))
    return samples


def samples_from_network_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Expose NIC/DPU inventory health as lightweight fabric presence gauges."""
    samples = []
    for row in rows:
        adapter = str(row.get("Id") or "adapter")
        dims = _with_dims(identity, source="network-adapter", adapter=_dim_value(adapter))
        dims["device_class"] = str(row.get("DeviceClass") or "NIC")
        if row.get("Model"):
            dims["model"] = _dim_value(row["Model"])
        samples.append(_sample("hw.fabric.adapter_present", 1.0, dims, None))
    return samples


def samples_from_component_integrity_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Expose ComponentIntegrity enabled state for attested fabric components."""
    samples = []
    for row in rows:
        component = str(row.get("Id") or "component")
        enabled = 1.0 if row.get("Enabled") is True else 0.0
        dims = _with_dims(identity, source="component-integrity", component=_dim_value(component))
        if row.get("Type"):
            dims["component_integrity_type"] = str(row["Type"])
        samples.append(_sample("hw.component_integrity.enabled", enabled, dims, None))
    return samples


def render_prometheus_text(samples: Iterable[MetricSample]) -> str:
    """Render samples in Prometheus/OpenMetrics text exposition form."""
    lines = []
    seen_types = set()
    for sample in samples:
        if sample.metric not in seen_types:
            lines.append(f"# TYPE {sample.metric} {sample.metric_type}")
            seen_types.add(sample.metric)
        label_text = ",".join(
            f'{key}="{_escape_label_value(value)}"'
            for key, value in sorted(sample.dimensions.items())
        )
        lines.append(f"{sample.metric}{{{label_text}}} {_format_value(sample.value)}")
    return "\n".join(lines) + "\n"


def to_signalfx_body(samples: Iterable[MetricSample]) -> dict[str, list[dict]]:
    """Wrap samples in the SignalFx /v2/datapoint gauge envelope."""
    return {
        "gauge": [
            {
                "metric": sample.metric,
                "value": sample.value,
                "dimensions": dict(sample.dimensions),
            }
            for sample in samples
        ]
    }


def _require_datapoint_url(ingest_url: str) -> str:
    """Return ``ingest_url`` when it is a full SignalFx datapoint endpoint, else raise.

    ``push_signalfx`` POSTs the URL as-is (it does not append a path), so a bare
    host such as ``https://ingest.us1.observability.splunkcloud.com`` accepts the
    request context but silently drops every datapoint. Require the full
    ``…/v2/datapoint`` endpoint so misconfiguration fails loudly instead.
    """
    if SIGNALFX_DATAPOINT_PATH not in (ingest_url or ""):
        raise ValueError(
            "SignalFx ingest URL must be the full datapoint endpoint ending in "
            f"{SIGNALFX_DATAPOINT_PATH} (e.g. "
            "https://ingest.us1.signalfx.com/v2/datapoint), not a bare host like "
            f"https://ingest.us1.observability.splunkcloud.com; got {ingest_url!r}"
        )
    return ingest_url


def resolve_signalfx_token(token_env: Optional[str] = None) -> str:
    """Return the SignalFx ingest token from ``token_env`` (default SPLUNK_ACCESS_TOKEN)."""
    name = token_env or "SPLUNK_ACCESS_TOKEN"
    token = os.environ.get(name, "")
    if not token:
        raise ValueError(f"{name} is not set")
    return token


def resolve_signalfx_ingest_url(ingest_url: Optional[str] = None) -> str:
    """Return a validated SignalFx datapoint ingest URL.

    Falls back to the ``SPLUNK_INGEST_URL`` environment variable and requires the
    full ``…/v2/datapoint`` endpoint (see ``_require_datapoint_url``).
    """
    url = ingest_url or os.environ.get("SPLUNK_INGEST_URL", "")
    if not url:
        raise ValueError("SPLUNK_INGEST_URL is not set")
    return _require_datapoint_url(url)


def push_signalfx(body: Mapping, token: str, ingest_url: str, timeout: float = 20.0) -> int:
    """POST a SignalFx datapoint body and return the status code.

    ``ingest_url`` must be the full SignalFx datapoint endpoint (``…/v2/datapoint``);
    it is POSTed verbatim, so a bare host silently drops every datapoint
    (see ``_require_datapoint_url``).
    """
    _require_datapoint_url(ingest_url)
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        ingest_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-SF-Token": token},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status


def serve_prometheus(
        scrape: Callable[[], str],
        bind: str = "0.0.0.0",
        port: int = 9109) -> None:
    """Serve ``/metrics`` by calling ``scrape`` for each request."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            if self.path != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            try:
                payload = scrape().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:  # noqa: BLE001 - exporter should return HTTP 500
                payload = f"exporter scrape failed: {type(exc).__name__}\n".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002 - http.server API
            return

    HTTPServer((bind, port), Handler).serve_forever()


def run_signalfx_loop(
        scrape_samples: Callable[[], list[MetricSample]],
        token: str,
        ingest_url: str,
        interval: float,
        timeout: float = 20.0) -> None:
    """Push SignalFx datapoints forever at ``interval`` seconds."""
    while True:
        start = time.monotonic()
        push_signalfx(to_signalfx_body(scrape_samples()), token, ingest_url, timeout=timeout)
        elapsed = time.monotonic() - start
        time.sleep(max(1.0, interval - elapsed))


def _reading(field):
    if isinstance(field, Mapping):
        return field.get("Reading")
    return field


def _fan_readings(row: Mapping) -> list[tuple[str, float]]:
    readings = []
    for fan in row.get("FanSpeedsPercent") or []:
        if not isinstance(fan, Mapping):
            continue
        rpm = _as_float(fan.get("SpeedRPM"))
        if rpm is None:
            continue
        name = str(fan.get("DeviceName") or fan.get("@odata.id") or "fan").rsplit("/", 1)[-1]
        readings.append((name, rpm))
    return readings


def _as_float(value) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text == "true":
            return 1.0
        if text == "false":
            return 0.0
        return None
    return parsed if math.isfinite(parsed) else None


def _sample(metric: str,
            value: float,
            dims: Mapping[str, str],
            unit: Optional[str] = None,
            timestamp: Optional[str] = None) -> MetricSample:
    return MetricSample(metric=metric, value=float(value),
                        dimensions={k: str(v) for k, v in dims.items()},
                        unit=unit, timestamp=timestamp)


def _with_dims(identity: Mapping[str, str], **extra) -> dict[str, str]:
    dims = {key: str(identity.get(key, "unknown")) for key in REQUIRED_DIMENSIONS}
    for key, value in extra.items():
        if value not in (None, ""):
            dims[key] = str(value)
    return dims


def _environment_chassis(row: Mapping) -> str:
    parent_type = row.get("ParentType")
    parent_id = row.get("ParentId")
    if parent_type == "Chassis" and parent_id:
        return str(parent_id)
    return str(row.get("Chassis") or row.get("Id") or "unknown")


def _environment_dims(identity: Mapping[str, str],
                      row: Mapping,
                      chassis: str) -> dict[str, str]:
    dims = _with_dims(identity, source="environment", chassis=chassis)
    parent_type = row.get("ParentType")
    parent_id = row.get("ParentId")
    if parent_type:
        dims["resource_type"] = str(parent_type)
    if parent_id:
        resource = _dim_value(parent_id)
        dims["resource"] = resource
        if parent_type == "Processor":
            dims["processor"] = resource
        elif parent_type == "Memory":
            dims["memory"] = resource
    return dims


def _environment_gpu(row: Mapping, chassis: str) -> Optional[str]:
    parent_type = row.get("ParentType")
    parent_id = str(row.get("ParentId") or "")
    if parent_type == "Processor" and parent_id.startswith("GPU_"):
        return parent_id
    if parent_type == "Memory":
        match = re.match(r"(GPU_\d+)", parent_id)
        if match:
            return match.group(1)
    return _gpu_from_chassis(chassis)


def _fabric_dims(identity: Mapping[str, str],
                 system,
                 gpu,
                 port,
                 fabric: str) -> dict[str, str]:
    dims = _with_dims(identity, source="fabric", fabric=fabric)
    for key, value in (("system", system), ("gpu", gpu), ("port", port)):
        if value:
            dims[key] = str(value)
    return dims


def _gpu_from_chassis(chassis: str) -> Optional[str]:
    parts = chassis.split("HGX_")
    if len(parts) == 2 and parts[1].startswith("GPU_"):
        return parts[1]
    return chassis if chassis.startswith("GPU_") else None


def _gpu_dim(chassis: str) -> dict[str, str]:
    gpu = _gpu_from_chassis(chassis)
    return {"gpu": gpu} if gpu else {}


def _parse_metric_property(prop: str) -> dict[str, str]:
    path, _, fragment = prop.partition("#")
    parts = [part for part in path.strip("/").split("/") if part]
    frag = [p for p in fragment.strip("/").split("/") if p] if fragment else []
    idx = None
    if frag:
        # a trailing numeric segment (e.g. .../NVDECUtilizationPercent/0) is an
        # array index, not the metric name — keep the name, expose the index
        if frag[-1].isdigit() and len(frag) >= 2:
            prop_name, idx = frag[-2], frag[-1]
        else:
            prop_name = frag[-1]
    else:
        prop_name = parts[-1] if parts else "metric"
    info = {"property": prop_name}
    if idx is not None:
        info["index"] = idx
    for collection, key in (("Systems", "system"), ("Processors", "gpu"),
                            ("Ports", "port"), ("Chassis", "chassis")):
        if collection in parts:
            i = parts.index(collection) + 1
            if i < len(parts):
                info[key] = parts[i]
    return info


def _unit_for_metric(metric: str) -> Optional[str]:
    if metric.endswith("_bytes"):
        return "By"
    if metric.endswith("_gbps") or metric.endswith("port_speed"):
        return "Gbps"
    return None


def _generic_metric_name(prop: str) -> str:
    """Vendor-neutral metric name for any MetricReport property not in the
    curated fabric map, so the full telemetry surface is exported rather than
    just fabric counters. e.g. ``FP16ActivityPercent`` -> ``hw.gb300.fp16_activity_percent``.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", prop)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    return f"hw.gb300.{snake or 'metric'}"


def _dim_value(value) -> str:
    cleaned = DIM_VALUE_OK.sub("_", str(value)).strip("_")
    return (cleaned or "unknown")[:256]


def _escape_label_value(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
