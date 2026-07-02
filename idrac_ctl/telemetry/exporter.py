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


def load_exporter_env_file(path: os.PathLike[str] | str) -> dict[str, str]:
    """Read a simple KEY=VALUE runtime env file without printing secret values."""
    values = {}
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"IDRAC_IP", "IDRAC_USERNAME", "IDRAC_PASSWORD", "IDRAC_PORT"}:
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
    file_path = file_path or os.environ.get("IDRAC_EXPORTER_CREDENTIAL_FILE")
    if not file_path:
        return
    values = load_exporter_env_file(file_path)
    mapping = {
        "IDRAC_IP": "idrac_ip",
        "IDRAC_USERNAME": "idrac_username",
        "IDRAC_PASSWORD": "idrac_password",
        "IDRAC_PORT": "idrac_port",
    }
    for env_name, attr in mapping.items():
        if env_name not in values:
            continue
        current = getattr(args, attr, "")
        if current in ("", None, "root") or env_name == "IDRAC_PASSWORD":
            value = values[env_name]
            setattr(args, attr, int(value) if env_name == "IDRAC_PORT" else value)


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
        chassis = str(row.get("Chassis") or row.get("Id") or "unknown")
        dims = _with_dims(identity, source="environment", chassis=chassis)
        power = _as_float(_reading(row.get("PowerWatts")))
        if power is not None:
            metric = "hw.gpu.power" if _gpu_from_chassis(chassis) else "hw.power"
            samples.append(_sample(metric, power, dims | _gpu_dim(chassis), unit="W"))
        energy = _as_float(_reading(row.get("EnergykWh") or row.get("EnergyKWh")))
        if energy is not None:
            samples.append(_sample("hw.energy_kwh", energy, dims | _gpu_dim(chassis), unit="kWh"))
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
    """Map TelemetryService MetricReport rows into GB300 fabric metrics."""
    samples = []
    for row in rows:
        prop = row.get("MetricProperty")
        if not prop:
            continue
        prop_info = _parse_metric_property(str(prop))
        if prop_info["property"] not in FABRIC_PROPERTY_METRICS:
            continue
        value = _as_float(row.get("MetricValue"))
        if value is None:
            continue
        metric = FABRIC_PROPERTY_METRICS[prop_info["property"]]
        fabric = "ib" if prop_info.get("port", "").lower().startswith("ib") else "nvlink"
        dims = _fabric_dims(identity, prop_info.get("system"),
                            prop_info.get("gpu"), prop_info.get("port"), fabric)
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
    info = {"property": (fragment.strip("/").split("/")[-1] if fragment else parts[-1])}
    for collection, key in (("Systems", "system"), ("Processors", "gpu"),
                            ("Ports", "port"), ("Chassis", "chassis")):
        if collection in parts:
            index = parts.index(collection) + 1
            if index < len(parts):
                info[key] = parts[index]
    return info


def _unit_for_metric(metric: str) -> Optional[str]:
    if metric.endswith("_bytes"):
        return "By"
    if metric.endswith("_gbps") or metric.endswith("port_speed"):
        return "Gbps"
    return None


def _dim_value(value) -> str:
    cleaned = DIM_VALUE_OK.sub("_", str(value)).strip("_")
    return (cleaned or "unknown")[:256]


def _escape_label_value(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
