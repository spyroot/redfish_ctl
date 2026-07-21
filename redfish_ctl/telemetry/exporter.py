"""Map Redfish telemetry rows into Prometheus and SignalFx metrics."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
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
GPU_COMPUTE_PROPERTIES = {
    "DMMAUtilizationPercent": "dmma",
    "FP16ActivityPercent": "fp16_activity",
    "FP32ActivityPercent": "fp32_activity",
    "FP64ActivityPercent": "fp64_activity",
    "GraphicsEngineActivityPercent": "graphics_engine_activity",
    "HMMAUtilizationPercent": "hmma",
    "IMMAUtilizationPercent": "imma",
    "IntegerActivityUtilizationPercent": "integer_activity",
    "NVDecInstanceUtilizationPercent": "nvdec_instance",
    "NVDecUtilizationPercent": "nvdec",
    "NVJpgInstanceUtilizationPercent": "nvjpg_instance",
    "NVJpgUtilizationPercent": "nvjpg",
    "NVOfaUtilizationPercent": "nvofa",
    "SMActivityPercent": "sm_activity",
    "SMOccupancyPercent": "sm_occupancy",
    "SMUtilizationPercent": "sm",
    "TensorCoreActivityPercent": "tensor_core_activity",
}
GPU_MEMORY_PROPERTIES = {
    "BandwidthPercent": ("hw.gpu.memory.bandwidth_utilization", "bandwidth", "%"),
    "CapacityUtilizationPercent": ("hw.gpu.memory.capacity_utilization", "capacity", "%"),
    "OperatingSpeedMHz": ("hw.gpu.memory.clock_mhz", "operating_speed", "MHz"),
}
GPU_MEMORY_ECC_PROPERTIES = {
    "CorrectableECCErrorCount": "correctable",
    "UncorrectableECCErrorCount": "uncorrectable",
}
GPU_MEMORY_ROW_REMAP_PROPERTIES = {
    "CorrectableRowRemappingCount": "correctable",
    "HighAvailabilityBankCount": "high_availability",
    "LowAvailabilityBankCount": "low_availability",
    "MaxAvailabilityBankCount": "max_availability",
    "NoAvailabilityBankCount": "no_availability",
    "PartialAvailabilityBankCount": "partial_availability",
    "UncorrectableRowRemappingCount": "uncorrectable",
}
GPU_THROTTLE_PROPERTIES = {
    "GlobalSoftwareViolationThrottleDuration": "global_software_violation",
    "HardwareViolationThrottleDuration": "hardware_violation",
    "PowerLimitThrottleDuration": "power_limit",
    "ThermalLimitThrottleDuration": "thermal_limit",
}
ISO_DURATION = re.compile(
    r"^P"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)
SECRET_ARG_NAMES = {"--idrac_password", "--idrac-password"}
DIM_VALUE_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
# push_signalfx POSTs the ingest URL as-is, so it must be the full SignalFx
# datapoint endpoint (…/v2/datapoint), never a bare host.
# One-hot state-metric label allowlists (specs/telemetry/gates.md, M1): each
# categorical row emits value 1 with a normalized lowercase label; values
# outside the allowlist map to "unknown" (health/state) or "other"
# (reason/reset_type) — never dropped, never free-form. A contract test
# asserts these sets match specs/telemetry/expected_signals.yaml.
HEALTH_LABELS = {"ok", "warning", "critical"}
STATE_LABELS = {"enabled", "disabled", "standby_offline", "standby_spare",
                "in_test", "starting", "absent", "unavailable_offline",
                "deferring", "quiesced", "updating", "qualified"}
LINK_DOWN_REASONS = {"peer_reset_event"}
RESET_TYPES = {"pf_flr", "conventional", "fundamental"}
EDP_STATES = {"normal", "asserted"}
POWER_BREAK_STATES = {"normal", "active"}

SIGNALFX_DATAPOINT_PATH = "/v2/datapoint"
POLL_JITTER_FRACTION = 0.10


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
    """Return the fixed join dimensions required on every exported series.

    :param bmc_ip: BMC management IP; its final octet derives the slot/node.
    :param vendor: hardware vendor label; lowercased into the ``vendor`` dimension.
    :param host_prefix: prefix for the derived ``host.name`` (``<prefix>-slot<n>``).
    :param bmc_octet_base: BMC last-octet offset subtracted to compute the slot number.
    :param server_octet_base: host last-octet offset added to the slot for ``server.address``.
    :param server_subnet: override for the first three octets of ``server.address``;
        defaults to the BMC subnet.
    :return: dict of ``host.name``, ``node``, ``server.address``, ``bmc.ip`` and
        ``vendor`` dimensions.
    """
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
_EXPORTER_CONFIG_FILE_ENVS = (
    "REDFISH_EXPORTER_CONFIG_FILE",
    "IDRAC_EXPORTER_CONFIG_FILE",
)
_IDENTITY_ENV_KEYS = {
    "host_prefix": ("REDFISH_EXPORTER_HOST_PREFIX", "IDRAC_EXPORTER_HOST_PREFIX"),
    "bmc_octet_base": (
        "REDFISH_EXPORTER_BMC_OCTET_BASE",
        "IDRAC_EXPORTER_BMC_OCTET_BASE",
    ),
    "server_octet_base": (
        "REDFISH_EXPORTER_SERVER_OCTET_BASE",
        "IDRAC_EXPORTER_SERVER_OCTET_BASE",
    ),
    "server_subnet": (
        "REDFISH_EXPORTER_SERVER_SUBNET",
        "IDRAC_EXPORTER_SERVER_SUBNET",
    ),
}


def load_exporter_env_file(path: os.PathLike[str] | str) -> dict[str, str]:
    """Read a simple KEY=VALUE runtime env file without printing secret values.

    Accepts REDFISH_IP/USERNAME/PASSWORD/PORT and the legacy IDRAC_* names.

    :param path: path to the credential env file to read.
    :return: mapping of recognized credential keys to their unquoted values.
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


def _non_empty(value):
    """Return ``value`` with blank strings collapsed to None.

    :param value: candidate config value.
    :return: stripped value, original non-string value, or None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _first_non_empty(*values):
    """Return the first non-empty value from ``values``.

    :param values: candidate values in precedence order.
    :return: the first non-empty value, or None.
    """
    for value in values:
        cleaned = _non_empty(value)
        if cleaned is not None:
            return cleaned
    return None


def _coerce_int(value, field_name: str) -> int:
    """Coerce an integer config field with a targeted error message.

    :param value: value to coerce.
    :param field_name: field name included in validation errors.
    :return: coerced integer value.
    :raises ValueError: when the value cannot be parsed as an integer.
    """
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer; got {value!r}") from exc


def _config_path(path: Optional[str] = None) -> Optional[str]:
    """Return the explicit or environment-provided exporter config path.

    :param path: explicit config path.
    :return: config path from argument or environment, or None.
    """
    return _first_non_empty(
        path,
        *(os.environ.get(name) for name in _EXPORTER_CONFIG_FILE_ENVS),
    )


def load_exporter_config_file(path: os.PathLike[str] | str) -> dict:
    """Read an exporter JSON config spec.

    :param path: JSON config file path.
    :return: parsed config mapping.
    :raises ValueError: when the config root is not a JSON object.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("exporter config root must be a JSON object")
    return data


def _section(config: Mapping, key: str) -> Mapping:
    """Return a nested config section mapping, or an empty mapping.

    :param config: exporter config mapping.
    :param key: nested section name.
    :return: nested section mapping, or an empty mapping.
    """
    value = config.get(key)
    return value if isinstance(value, Mapping) else {}


def _config_value(config: Mapping, section: str, top_key: str, section_key: str):
    """Return a top-level or nested config value.

    :param config: exporter config mapping.
    :param section: nested section name to inspect first.
    :param top_key: top-level fallback key.
    :param section_key: key inside the nested section.
    :return: configured value, or None.
    """
    nested = _section(config, section)
    if section_key in nested:
        return nested[section_key]
    return config.get(top_key)


def exporter_config_options(path: Optional[str] = None) -> dict:
    """Return flattened exporter options from an optional JSON spec file.

    The spec may use nested ``signalfx`` and ``identity`` objects or the flat
    CLI-style keys used by tests and programmatic callers.

    :param path: explicit config file path; falls back to exporter config env vars.
    :return: flattened option names understood by ``Exporter.execute``.
    """
    file_path = _config_path(path)
    if not file_path:
        return {}
    config = load_exporter_config_file(file_path)
    candidates = {
        "signalfx_ingest_url": _config_value(
            config, "signalfx", "signalfx_ingest_url", "ingest_url"),
        "signalfx_token_env": _config_value(
            config, "signalfx", "signalfx_token_env", "token_env"),
        "signalfx_token_file": _config_value(
            config, "signalfx", "signalfx_token_file", "token_file"),
        "signalfx_token": _config_value(
            config, "signalfx", "signalfx_token", "token"),
        "identity_host_prefix": _config_value(
            config, "identity", "identity_host_prefix", "host_prefix"),
        "identity_bmc_octet_base": _config_value(
            config, "identity", "identity_bmc_octet_base", "bmc_octet_base"),
        "identity_server_octet_base": _config_value(
            config, "identity", "identity_server_octet_base", "server_octet_base"),
        "identity_server_subnet": _config_value(
            config, "identity", "identity_server_subnet", "server_subnet"),
    }
    return {
        key: value
        for key, value in candidates.items()
        if _non_empty(value) is not None
    }


def exporter_argv_uses_secret(argv: Iterable[str]) -> bool:
    """True when the exporter invocation carries a password on argv.

    :param argv: command-line arguments to inspect.
    :return: True if an exporter invocation passes a password flag on argv, else False.
    """
    args = list(argv)
    if "exporter" not in args:
        return False
    for arg in args:
        if any(arg == name or arg.startswith(f"{name}=") for name in SECRET_ARG_NAMES):
            return True
    return False


def apply_exporter_env_file(args, path: Optional[str] = None) -> None:
    """Apply exporter credential-file values to an argparse namespace in place.

    :param args: argparse namespace updated in place with credential values.
    :param path: explicit env-file path; falls back to the namespace attribute and
        the REDFISH_/IDRAC_ exporter credential-file environment variables.
    """
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


def resolve_identity_options(
        host_prefix: Optional[str] = None,
        bmc_octet_base: Optional[int] = None,
        server_octet_base: Optional[int] = None,
        server_subnet: Optional[str] = None) -> dict:
    """Resolve exporter identity dimension options from args, env, and defaults.

    :param host_prefix: explicit ``host.name`` prefix override.
    :param bmc_octet_base: explicit BMC last-octet base used to derive slot.
    :param server_octet_base: explicit server last-octet base used to derive host IP.
    :param server_subnet: explicit server subnet for ``server.address``.
    :return: keyword arguments for :func:`build_identity_dimensions`.
    """
    resolved_host_prefix = _first_non_empty(
        host_prefix,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["host_prefix"]),
        "gb300-poc1",
    )
    resolved_bmc_octet_base = _first_non_empty(
        bmc_octet_base,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["bmc_octet_base"]),
        20,
    )
    resolved_server_octet_base = _first_non_empty(
        server_octet_base,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["server_octet_base"]),
        40,
    )
    resolved_server_subnet = _first_non_empty(
        server_subnet,
        *(os.environ.get(name) for name in _IDENTITY_ENV_KEYS["server_subnet"]),
    )
    return {
        "host_prefix": str(resolved_host_prefix),
        "bmc_octet_base": _coerce_int(resolved_bmc_octet_base, "bmc_octet_base"),
        "server_octet_base": _coerce_int(
            resolved_server_octet_base, "server_octet_base"),
        "server_subnet": (
            str(resolved_server_subnet)
            if resolved_server_subnet is not None
            else None
        ),
    }


def build_metric_samples(
        identity: Mapping[str, str],
        environment_rows: Iterable[Mapping],
        sensor_rows: Iterable[Mapping],
        nvlink_rows: Iterable[Mapping],
        metric_report_rows: Iterable[Mapping],
        thermal_rows: Iterable[Mapping] = (),
        leak_detection_rows: Iterable[Mapping] = (),
        network_rows: Iterable[Mapping] = (),
        component_integrity_rows: Iterable[Mapping] = ()) -> list[MetricSample]:
    """Build exporter samples from normalized Redfish command rows.

    :param identity: fixed join dimensions applied to every sample.
    :param environment_rows: Chassis EnvironmentMetrics rows (power/energy/fan).
    :param sensor_rows: Redfish Sensor rows (thermal/fan/voltage/power).
    :param nvlink_rows: nvlink-ports rows for per-link fabric metrics.
    :param metric_report_rows: TelemetryService MetricReport rows.
    :param thermal_rows: ThermalSubsystem temperature rows.
    :param leak_detection_rows: LeakDetector rows.
    :param network_rows: NIC/DPU network-adapter inventory rows.
    :param component_integrity_rows: ComponentIntegrity rows.
    :return: combined list of MetricSample objects from all row sources.
    """
    samples: list[MetricSample] = []
    samples.extend(samples_from_environment_rows(environment_rows, identity))
    samples.extend(samples_from_sensor_rows(sensor_rows, identity))
    samples.extend(samples_from_nvlink_rows(nvlink_rows, identity))
    samples.extend(samples_from_thermal_rows(thermal_rows, identity))
    samples.extend(samples_from_metric_report_rows(metric_report_rows, identity))
    samples.extend(samples_from_leak_detection_rows(leak_detection_rows, identity))
    samples.extend(samples_from_network_rows(network_rows, identity))
    samples.extend(samples_from_component_integrity_rows(component_integrity_rows, identity))
    return samples


def scrape_health_samples(
        identity: Mapping[str, str],
        ok: bool,
        duration_seconds: float) -> list[MetricSample]:
    """Return per-scrape liveness and duration samples.

    :param identity: fixed join dimensions applied to the health samples.
    :param ok: whether the scrape succeeded (1.0) or failed (0.0).
    :param duration_seconds: scrape wall-clock duration, in seconds.
    :return: list of the ``hw.scrape.ok`` and ``hw.scrape.duration_seconds`` samples.
    """
    dims = _with_dims(identity, source="exporter")
    duration = _as_float(duration_seconds)
    return [
        _sample("hw.scrape.ok", 1.0 if ok else 0.0, dims, None),
        _sample(
            "hw.scrape.duration_seconds",
            max(0.0, duration if duration is not None else 0.0),
            dims,
            "s",
        ),
    ]


def jittered_interval(
        interval: float,
        jitter_fraction: float = POLL_JITTER_FRACTION,
        random_value: Optional[float] = None) -> float:
    """Return ``interval`` offset by a bounded symmetric jitter fraction.

    :param interval: base interval in seconds; non-positive values fall back to 1.0.
    :param jitter_fraction: symmetric jitter as a fraction of the interval; negatives clamp to 0.
    :param random_value: optional draw in [0, 1] to use instead of ``random.random()``.
    :return: the interval adjusted by the bounded jitter, in seconds.
    """
    base = _as_float(interval)
    if base is None or base <= 0:
        base = 1.0
    fraction = _as_float(jitter_fraction)
    if fraction is None or fraction < 0:
        fraction = 0.0
    draw = random.random() if random_value is None else random_value
    try:
        bounded = min(1.0, max(0.0, float(draw)))
    except (TypeError, ValueError):
        bounded = 0.5
    return base * (1.0 - fraction + (2.0 * fraction * bounded))


def samples_from_environment_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map Chassis EnvironmentMetrics rows into chassis/GPU power metrics.

    :param rows: Chassis EnvironmentMetrics rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: power, energy and fan-speed samples derived from the rows.
    """
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
    """Map Redfish Sensor rows into chassis thermal/fan/voltage/GPU power metrics.

    :param rows: Redfish Sensor rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: thermal, fan, voltage and power samples derived from the rows.
    """
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
    """Map nvlink-ports rows into per-link fabric metrics.

    :param rows: nvlink-ports rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-link fabric samples (link state, speed, byte counters, BER).
    """
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
    every other numeric property (GPU FP16/FP32 activity, thermal, power,
    memory, …) is emitted under a generic ``hw.gb300.*`` name so the FULL
    telemetry surface reaches OTel/Prometheus, not just the fabric subset.
    Categorical rows (Health, HealthRollup, State, LinkDownReasonCode,
    EDPViolationState, PowerBreakPerformanceState, LastResetType) are mapped
    to one-hot state samples by :func:`_state_enum_sample` per the M1 model
    in ``specs/telemetry/gates.md`` instead of being dropped.

    :param rows: TelemetryService MetricReport rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: one MetricSample per convertible MetricReport row.
    """
    samples = []
    for row in rows:
        prop = row.get("MetricProperty")
        if not prop:
            continue
        prop_info = _parse_metric_property(str(prop))
        prop_name = prop_info["property"]
        gpu_sample = _gpu_metric_report_sample(prop_info, row, identity)
        if gpu_sample is not None:
            samples.append(gpu_sample)
            continue

        value = _as_float(row.get("MetricValue"))
        if value is None:
            state_sample = _state_enum_sample(prop_info, row, identity)
            if state_sample is not None:
                samples.append(state_sample)
            continue
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


def _state_label(text: str) -> str:
    """Normalize an enum string to a lowercase snake_case label value.

    :param text: raw vendor enum text (for example ``PeerResetEvent``).
    :return: normalized label (for example ``peer_reset_event``).
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    return snake or "unknown"


def _state_enum_sample(
        prop_info: Mapping[str, str],
        row: Mapping,
        identity: Mapping[str, str]) -> Optional[MetricSample]:
    """Map a categorical MetricReport row to a one-hot state sample.

    Implements the M1 model from ``specs/telemetry/gates.md``: every known
    state/health enum row emits value 1 with a normalized, allowlisted
    lowercase label — ``Health``/``HealthRollup`` → ``hw.component.health`` /
    ``hw.component.health_rollup`` (``health`` label), ``State`` →
    ``hw.component.state`` (``state`` label), ``LinkDownReasonCode`` →
    ``hw.fabric.link_down_reason`` (``reason`` label — the WHY behind
    link-down counters), ``EDPViolationState`` →
    ``hw.power.edp_violation_state``, ``PowerBreakPerformanceState`` →
    ``hw.power.break_performance_state``, ``LastResetType`` →
    ``hw.component.last_reset_type`` (``reset_type`` label). Values outside
    an allowlist map to ``unknown`` (health/state) or ``other``
    (reason/reset_type/power states) so no vendor string is ever dropped and
    no free-form text ever becomes a label.

    :param prop_info: parsed MetricProperty fields (property, system, gpu, port, …).
    :param row: the raw MetricReport row.
    :param identity: fixed join dimensions applied to the sample.
    :return: the mapped MetricSample, or None when the row is not a known
        categorical property (or its value is empty).
    """
    prop_name = prop_info["property"]
    text = str(row.get("MetricValue") or "").strip()
    if not text:
        return None
    label = _state_label(text)
    dims = _with_dims(identity, source="metric-report",
                      property=_dim_value(prop_name))
    for key in ("system", "gpu", "port", "chassis", "memory", "index"):
        if prop_info.get(key):
            dims[key] = str(prop_info[key])
    dims["report"] = str(row.get("Report") or "unknown")

    if prop_name in ("Health", "HealthRollup"):
        metric = ("hw.component.health" if prop_name == "Health"
                  else "hw.component.health_rollup")
        dims["health"] = label if label in HEALTH_LABELS else "unknown"
    elif prop_name == "State":
        metric = "hw.component.state"
        dims["state"] = label if label in STATE_LABELS else "unknown"
    elif prop_name == "LinkDownReasonCode":
        metric = "hw.fabric.link_down_reason"
        dims["reason"] = label if label in LINK_DOWN_REASONS else "other"
        dims["fabric"] = ("ib" if str(prop_info.get("port", "")).lower().startswith("ib")
                          else "nvlink")
    elif prop_name == "EDPViolationState":
        metric = "hw.power.edp_violation_state"
        dims["state"] = label if label in EDP_STATES else "other"
    elif prop_name == "PowerBreakPerformanceState":
        metric = "hw.power.break_performance_state"
        dims["state"] = label if label in POWER_BREAK_STATES else "other"
    elif prop_name == "LastResetType":
        metric = "hw.component.last_reset_type"
        dims["reset_type"] = label if label in RESET_TYPES else "other"
    else:
        return None
    return _sample(metric, 1.0, dims, None, row.get("Timestamp"))


def _gpu_metric_report_sample(
        prop_info: Mapping[str, str],
        row: Mapping,
        identity: Mapping[str, str]) -> Optional[MetricSample]:
    """Build a GPU-specific MetricSample from a MetricReport row, if applicable.

    :param prop_info: parsed MetricProperty fields (property, source, gpu, index, …).
    :param row: the raw MetricReport row.
    :param identity: fixed join dimensions applied to the sample.
    :return: a GPU temperature/clock/utilization/throttle/memory sample, or None
        when the row is not a recognized GPU metric.
    """
    prop_name = str(prop_info.get("property") or "")
    gpu = _gpu_from_metric_info(prop_info)
    if not gpu:
        return None

    source = prop_info.get("metric_source")
    value = _as_float(row.get("MetricValue"))
    dims = _gpu_metric_dims(identity, prop_info, row, gpu)

    if source == "sensor" and _is_gpu_temperature(prop_name):
        if value is None:
            return None
        return _sample(
            "hw.gpu.temperature",
            value,
            dims | {"property": "temperature", "sensor": _dim_value(prop_name)},
            "Cel",
            row.get("Timestamp"),
        )

    if source == "processor":
        if prop_name == "OperatingSpeedMHz" and value is not None:
            return _sample(
                "hw.gpu.clock_mhz",
                value,
                dims | {"property": "operating_speed"},
                "MHz",
                row.get("Timestamp"),
            )
        if prop_name in GPU_COMPUTE_PROPERTIES and value is not None:
            metric_dims = {
                "property": GPU_COMPUTE_PROPERTIES[prop_name],
            }
            if prop_info.get("index"):
                metric_dims["index"] = str(prop_info["index"])
            return _sample(
                "hw.gpu.compute.utilization",
                value,
                dims | metric_dims,
                "%",
                row.get("Timestamp"),
            )
        if prop_name in GPU_THROTTLE_PROPERTIES:
            seconds = _duration_seconds(row.get("MetricValue"))
            if seconds is None:
                return None
            return _sample(
                "hw.gpu.throttle.duration_seconds",
                seconds,
                dims | {"property": GPU_THROTTLE_PROPERTIES[prop_name]},
                "s",
                row.get("Timestamp"),
            )

    if source == "memory":
        if prop_name in GPU_MEMORY_PROPERTIES and value is not None:
            metric, property_name, unit = GPU_MEMORY_PROPERTIES[prop_name]
            return _sample(
                metric,
                value,
                dims | {"property": property_name},
                unit,
                row.get("Timestamp"),
            )
        if prop_name in GPU_MEMORY_ECC_PROPERTIES and value is not None:
            return _sample(
                "hw.gpu.memory.ecc_errors",
                value,
                dims | {"property": GPU_MEMORY_ECC_PROPERTIES[prop_name]},
                None,
                row.get("Timestamp"),
            )
        if prop_name in GPU_MEMORY_ROW_REMAP_PROPERTIES and value is not None:
            return _sample(
                "hw.gpu.memory.row_remap_count",
                value,
                dims | {"property": GPU_MEMORY_ROW_REMAP_PROPERTIES[prop_name]},
                None,
                row.get("Timestamp"),
            )
        if prop_name == "RowRemappingFailed" and value is not None:
            return _sample(
                "hw.gpu.memory.row_remapping_failed",
                value,
                dims | {"property": "row_remapping_failed"},
                None,
                row.get("Timestamp"),
            )

    return None


def _gpu_metric_dims(
        identity: Mapping[str, str],
        prop_info: Mapping[str, str],
        row: Mapping,
        gpu: str) -> dict[str, str]:
    """Build the GPU metric-report dimensions for one sample.

    :param identity: fixed join dimensions applied to the sample.
    :param prop_info: parsed MetricProperty fields providing system/chassis/memory context.
    :param row: the raw MetricReport row (supplies the report name).
    :param gpu: the resolved GPU identifier.
    :return: dimension mapping for the GPU sample.
    """
    dims = _with_dims(identity, source="metric-report", gpu=gpu)
    for key in ("system", "chassis", "memory"):
        if prop_info.get(key):
            dims[key] = str(prop_info[key])
    if row.get("Report"):
        dims["report"] = str(row["Report"])
    return dims


def samples_from_thermal_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map ThermalSubsystem temperature readings into per-zone metrics.

    :param rows: ThermalSubsystem temperature rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-zone ``hw.temperature`` samples derived from the rows.
    """
    samples = []
    for row in rows:
        reading = (row.get("ReadingCelsius")
                   if row.get("ReadingCelsius") is not None
                   else row.get("Reading"))
        value = _as_float(reading)
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        name = str(row.get("DeviceName") or row.get("Name")
                   or row.get("DataSourceUri") or "temperature").rsplit("/", 1)[-1]
        zone = row.get("PhysicalContext") or name
        dims = _with_dims(identity, source="thermal-subsystem",
                          chassis=chassis, sensor=_dim_value(name),
                          zone=_dim_value(zone))
        samples.append(_sample("hw.temperature", value, dims, "Cel"))
    return samples


def samples_from_leak_detection_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map LeakDetector rows into per-detector leak-state gauges.

    :param rows: LeakDetector rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-detector ``hw.leak.state`` samples derived from the rows.
    """
    samples = []
    for row in rows:
        value = _leak_state_value(row.get("DetectorState"))
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        detector = str(row.get("Id") or row.get("Name") or row.get("Uri") or "detector")
        dims = _with_dims(
            identity,
            source="leak-detector",
            chassis=chassis,
            detector=_dim_value(detector),
            detector_state=_dim_value(row.get("DetectorState")),
        )
        if row.get("LeakDetectorType"):
            dims["detector_type"] = _dim_value(row["LeakDetectorType"])
        if row.get("Health"):
            dims["health"] = _dim_value(row["Health"])
        if row.get("State"):
            dims["state"] = _dim_value(row["State"])
        samples.append(_sample("hw.leak.state", value, dims, None))
    return samples


def samples_from_network_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Expose NIC/DPU inventory health as lightweight fabric presence gauges.

    :param rows: network-adapter inventory rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: ``hw.fabric.adapter_present`` presence samples for each adapter.
    """
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
    """Expose ComponentIntegrity enabled state for attested fabric components.

    :param rows: ComponentIntegrity rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: ``hw.component_integrity.enabled`` samples for each component.
    """
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
    """Render samples in Prometheus/OpenMetrics text exposition form.

    :param samples: metric samples to render.
    :return: Prometheus/OpenMetrics text exposition of the samples.
    """
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
    """Wrap samples in the SignalFx /v2/datapoint gauge envelope.

    :param samples: metric samples to wrap.
    :return: SignalFx ``/v2/datapoint`` body with a ``gauge`` list.
    """
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


# Splunk Observability ingest host: accepts a datapoint POST and returns 200/"OK"
# but never records the metric time series. SignalFx datapoint ingest requires the
# ingest.<realm>.signalfx.com host instead.
_OBSERVABILITY_INGEST_HOST = re.compile(
    r"ingest\.([a-z0-9-]+)\.observability\.splunkcloud\.com", re.IGNORECASE)


def _normalize_signalfx_ingest_url(ingest_url: str) -> str:
    """Rewrite a Splunk Observability ingest host to the SignalFx datapoint host.

    ``ingest.<realm>.observability.splunkcloud.com`` is replaced with
    ``ingest.<realm>.signalfx.com``; the realm, scheme, path, and port are kept.
    Any other host is returned unchanged.

    :param ingest_url: the ingest URL to normalize.
    :return: the URL with the observability host rewritten, else unchanged.
    """
    return _OBSERVABILITY_INGEST_HOST.sub(r"ingest.\1.signalfx.com", ingest_url or "")


def _require_datapoint_url(ingest_url: str) -> str:
    """Return ``ingest_url`` when it is a full SignalFx datapoint endpoint, else raise.

    ``push_signalfx`` POSTs the URL as-is (it does not append a path), so a bare
    host such as ``https://ingest.us1.observability.splunkcloud.com`` accepts the
    request context but silently drops every datapoint. Require the full
    ``…/v2/datapoint`` endpoint, and reject the Observability ingest host outright,
    so misconfiguration fails loudly instead.

    :param ingest_url: the SignalFx ingest URL to validate.
    :return: ``ingest_url`` unchanged when it is a full datapoint endpoint.
    :raises ValueError: if the URL is not a full ``…/v2/datapoint`` endpoint, or it
        targets the Observability ingest host that silently drops datapoints.
    """
    if SIGNALFX_DATAPOINT_PATH not in (ingest_url or ""):
        raise ValueError(
            "SignalFx ingest URL must be the full datapoint endpoint ending in "
            f"{SIGNALFX_DATAPOINT_PATH} (e.g. "
            "https://ingest.us1.signalfx.com/v2/datapoint), not a bare host like "
            f"https://ingest.us1.observability.splunkcloud.com; got {ingest_url!r}"
        )
    if _OBSERVABILITY_INGEST_HOST.search(ingest_url):
        raise ValueError(
            "SignalFx ingest URL targets the Splunk Observability host "
            "(ingest.<realm>.observability.splunkcloud.com), which returns 200/OK "
            "but drops every datapoint; use ingest.<realm>.signalfx.com instead; "
            f"got {ingest_url!r}"
        )
    return ingest_url


def resolve_signalfx_token(
        token_env: Optional[str] = None,
        token: Optional[str] = None,
        token_file: Optional[str] = None) -> str:
    """Return the SignalFx ingest token from direct, file, or env source.

    :param token_env: env var name to read the token from; defaults to ``SPLUNK_ACCESS_TOKEN``.
    :param token: direct token value.
    :param token_file: path to a file containing the token.
    :return: the ingest token value.
    :raises ValueError: if the chosen source is unset or empty.
    """
    direct_token = _non_empty(token)
    if direct_token is not None:
        return str(direct_token)
    file_path = _non_empty(token_file)
    if file_path is not None:
        value = Path(str(file_path)).expanduser().read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"{file_path} is empty")
        return value
    name = token_env or "SPLUNK_ACCESS_TOKEN"
    token = os.environ.get(name, "")
    if not token:
        raise ValueError(f"{name} is not set")
    return token


def resolve_signalfx_ingest_url(ingest_url: Optional[str] = None) -> str:
    """Return a validated SignalFx datapoint ingest URL.

    Falls back to the ``SPLUNK_INGEST_URL`` environment variable and requires the
    full ``…/v2/datapoint`` endpoint (see ``_require_datapoint_url``).

    :param ingest_url: explicit ingest URL; falls back to ``SPLUNK_INGEST_URL``.
    :return: a validated full ``…/v2/datapoint`` ingest URL (Observability host
        normalized to the SignalFx datapoint host).
    :raises ValueError: if no URL is set or it is not a full datapoint endpoint.
    """
    url = ingest_url or os.environ.get("SPLUNK_INGEST_URL", "")
    if not url:
        raise ValueError("SPLUNK_INGEST_URL is not set")
    normalized = _normalize_signalfx_ingest_url(url)
    if normalized != url:
        # Never silent: surface the rewrite so a deploy dry-run shows the real target.
        print(
            f"note: rewrote SignalFx ingest host to the datapoint host {normalized} "
            "(the observability.splunkcloud.com host returns OK but drops datapoints)",
            file=sys.stderr,
        )
    return _require_datapoint_url(normalized)


def push_signalfx(body: Mapping, token: str, ingest_url: str, timeout: float = 20.0) -> int:
    """POST a SignalFx datapoint body and return the status code.

    ``ingest_url`` must be the full SignalFx datapoint endpoint (``…/v2/datapoint``);
    it is POSTed verbatim, so a bare host silently drops every datapoint
    (see ``_require_datapoint_url``).

    :param body: SignalFx datapoint payload to POST.
    :param token: SignalFx ingest token for the ``X-SF-Token`` header.
    :param ingest_url: full SignalFx datapoint endpoint (``…/v2/datapoint``).
    :param timeout: request timeout in seconds.
    :return: the HTTP status code of the POST response.
    :raises ValueError: if ``ingest_url`` is not a full datapoint endpoint.
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


def signalfx_metric_readback(
        realm: str, api_token: str, metric: str, timeout: float = 20.0) -> dict:
    """Return how many time series exist for a metric in Splunk MTS, and how fresh.

    A SignalFx datapoint POST returns HTTP 200/``OK`` even when the datapoints are
    dropped (for example the Observability ingest host, see issue #363), so ingest
    success must be confirmed by reading the metric time series back — not by
    trusting the POST status.

    :param realm: Splunk Observability realm (for example ``us1``).
    :param api_token: API (read) token, sent as the ``X-SF-Token`` header; never logged.
    :param metric: SignalFx metric name to look up.
    :param timeout: HTTP timeout in seconds.
    :return: ``{"count": <matching series>, "newest_ms": <latest update ms, 0 if none>}``.
    :raises ValueError: when the API answers with a non-JSON body.
    """
    query = urllib.parse.urlencode(
        {"query": f'sf_metric:"{metric}"', "limit": 50, "orderBy": "-sf_updatedOnMs"})
    url = f"https://api.{realm}.signalfx.com/v2/metrictimeseries?{query}"
    request = urllib.request.Request(url, headers={"X-SF-Token": api_token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"non-JSON metrictimeseries response for {metric}") from exc
    results = data.get("results") or []
    newest = 0
    for row in results:
        for key in ("lastUpdated", "sf_updatedOnMs", "updatedOnMs", "created"):
            stamp = row.get(key)
            if isinstance(stamp, (int, float)) and stamp > newest:
                newest = int(stamp)
    return {"count": int(data.get("count") or len(results)), "newest_ms": newest}


def verify_signalfx_readback(
        realm: str, api_token: str, metrics: Iterable[str],
        timeout: float = 20.0) -> dict:
    """Confirm each pushed metric is visible in Splunk MTS.

    :param realm: Splunk Observability realm.
    :param api_token: API (read) token; never logged.
    :param metrics: metric names to confirm (the names that were pushed).
    :param timeout: per-query HTTP timeout in seconds.
    :return: ``{metric: {"count": int, "newest_ms": int}}`` for each metric.
    """
    return {metric: signalfx_metric_readback(realm, api_token, metric, timeout)
            for metric in sorted(set(metrics))}


def build_readback_result(
        push_status: int, ingest_url: str, sample_count: int,
        metric_names: list, readback: dict, timing_ms: dict) -> tuple:
    """Build the compact canary summary and verdict from a readback.

    A metric is visible when its readback ``count`` is greater than zero. When any
    pushed metric is missing, the verdict is an error: the POST succeeded but the
    datapoints were not ingested (issue #363), so a 200 is not proof.

    :param push_status: the HTTP status the SignalFx POST returned.
    :param ingest_url: the (normalized) ingest URL that was POSTed to.
    :param sample_count: number of samples scraped and pushed.
    :param metric_names: the distinct metric names that were pushed.
    :param readback: ``{metric: {"count": int, "newest_ms": int}}`` from MTS.
    :param timing_ms: ``{"scrape": int, "push": int, "readback": int}`` durations.
    :return: ``(summary_dict, error_or_None)`` — the compact result and verdict.
    """
    visible = sorted(name for name, series in readback.items() if series["count"] > 0)
    missing = sorted(set(metric_names) - set(visible))
    summary = {
        "push_status": push_status,
        "ingest_url": ingest_url,
        "sample_count": sample_count,
        "metrics_pushed": len(metric_names),
        "metrics_visible": len(visible),
        "missing_metrics": missing,
        "readback": readback,
        "timing_ms": timing_ms,
    }
    error = None if not missing else (
        f"SignalFx POST returned {push_status} but {len(missing)} of "
        f"{len(metric_names)} pushed metrics have no time series in Splunk MTS — "
        "the POST succeeded yet datapoints were not ingested")
    return summary, error


def _report_signalfx_loop_error(exc: Exception) -> None:
    """Report a failed SignalFx push without stopping the exporter loop.

    :param exc: exception raised while scraping or pushing a SignalFx datapoint batch.
    """
    print(f"SignalFx push failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def serve_prometheus(
        scrape: Callable[[], str],
        bind: str = "0.0.0.0",
        port: int = 9109) -> None:
    """Serve ``/metrics`` by calling ``scrape`` for each request.

    :param scrape: callable returning the Prometheus text body for each request.
    :param bind: address to bind the HTTP server to.
    :param port: TCP port to serve ``/metrics`` on.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            """Serve ``/metrics`` with the scrape body, or 404/500 on error."""
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
            """Silence the default per-request stderr logging.

            :param format: log format string (ignored).
            """
            return

    HTTPServer((bind, port), Handler).serve_forever()


def run_signalfx_loop(
        scrape_samples: Callable[[], list[MetricSample]],
        token: str,
        ingest_url: str,
        interval: float,
        timeout: float = 20.0,
        on_error: Optional[Callable[[Exception], None]] = None) -> None:
    """Push SignalFx datapoints forever at ``interval`` seconds.

    :param scrape_samples: callable returning the samples to push each cycle.
    :param token: SignalFx ingest token.
    :param ingest_url: full SignalFx datapoint endpoint.
    :param interval: base seconds between pushes (jittered per cycle).
    :param timeout: per-push request timeout in seconds.
    :param on_error: optional callback for transient scrape or push failures.
    """
    report_error = on_error or _report_signalfx_loop_error
    while True:
        start = time.monotonic()
        try:
            push_signalfx(
                to_signalfx_body(scrape_samples()),
                token,
                ingest_url,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - exporter must survive transient push failures
            report_error(exc)
        elapsed = time.monotonic() - start
        time.sleep(max(1.0, jittered_interval(interval) - elapsed))


def _reading(field):
    """Return the ``Reading`` of a mapping field, or the field itself.

    :param field: a Redfish reading value or ``{"Reading": …}`` mapping.
    :return: the scalar reading value.
    """
    if isinstance(field, Mapping):
        return field.get("Reading")
    return field


def _fan_readings(row: Mapping) -> list[tuple[str, float]]:
    """Extract (name, RPM) pairs from a row's ``FanSpeedsPercent`` list.

    :param row: an EnvironmentMetrics row that may carry fan-speed entries.
    :return: list of (fan name, RPM) tuples with a numeric SpeedRPM.
    """
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
    """Coerce a Redfish value to a finite float, or None.

    :param value: the value to convert (bool, number, or string).
    :return: the float value, or None when it is missing or non-finite.
    """
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


def _duration_seconds(value) -> Optional[float]:
    """Convert a numeric value or ISO-8601 duration to seconds.

    :param value: a number or ISO-8601 duration string (e.g. ``PT5M``).
    :return: total seconds, or None when it cannot be parsed.
    """
    parsed = _as_float(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    match = ISO_DURATION.match(text)
    if not match:
        return None
    total = 0.0
    multipliers = {
        "days": 86400.0,
        "hours": 3600.0,
        "minutes": 60.0,
        "seconds": 1.0,
    }
    for name, multiplier in multipliers.items():
        amount = match.group(name)
        if amount:
            total += float(amount) * multiplier
    return total if math.isfinite(total) else None


def _leak_state_value(value) -> Optional[float]:
    """Map a leak-detector state string to a gauge value.

    :param value: the ``DetectorState`` string.
    :return: 0.0 for a clear state, 1.0 for a leak, or None when empty.
    """
    if value in (None, ""):
        return None
    state = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    clear_states = {
        "ok",
        "normal",
        "none",
        "absent",
        "notdetected",
        "noleak",
        "noleakdetected",
    }
    return 0.0 if state in clear_states else 1.0


def _sample(metric: str,
            value: float,
            dims: Mapping[str, str],
            unit: Optional[str] = None,
            timestamp: Optional[str] = None) -> MetricSample:
    """Construct a MetricSample with stringified dimension values.

    :param metric: metric name.
    :param value: numeric sample value.
    :param dims: dimension mapping.
    :param unit: optional unit annotation.
    :param timestamp: optional sample timestamp.
    :return: the assembled MetricSample.
    """
    return MetricSample(metric=metric, value=float(value),
                        dimensions={k: str(v) for k, v in dims.items()},
                        unit=unit, timestamp=timestamp)


def _with_dims(identity: Mapping[str, str], **extra) -> dict[str, str]:
    """Build a dimension dict from identity plus non-empty extras.

    :param identity: fixed join dimensions to seed the result.
    :return: dimension mapping with the required dims plus any non-empty extras.
    """
    dims = {key: str(identity.get(key, "unknown")) for key in REQUIRED_DIMENSIONS}
    for key, value in extra.items():
        if value not in (None, ""):
            dims[key] = str(value)
    return dims


def _environment_chassis(row: Mapping) -> str:
    """Resolve the chassis identifier for an EnvironmentMetrics row.

    :param row: the EnvironmentMetrics row.
    :return: the parent chassis id, falling back to Chassis/Id or ``unknown``.
    """
    parent_type = row.get("ParentType")
    parent_id = row.get("ParentId")
    if parent_type == "Chassis" and parent_id:
        return str(parent_id)
    return str(row.get("Chassis") or row.get("Id") or "unknown")


def _environment_dims(identity: Mapping[str, str],
                      row: Mapping,
                      chassis: str) -> dict[str, str]:
    """Build environment-source dimensions for an EnvironmentMetrics row.

    :param identity: fixed join dimensions to seed the result.
    :param row: the EnvironmentMetrics row supplying parent type/id.
    :param chassis: the resolved chassis identifier.
    :return: dimension mapping including resource/processor/memory context.
    """
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
    """Resolve the GPU identifier owning an EnvironmentMetrics row, if any.

    :param row: the EnvironmentMetrics row.
    :param chassis: the resolved chassis identifier.
    :return: the ``GPU_<n>`` identifier, or None when the row is not GPU-scoped.
    """
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
    """Build fabric-source dimensions for a link/port sample.

    :param identity: fixed join dimensions to seed the result.
    :param system: system identifier, if known.
    :param gpu: GPU identifier, if known.
    :param port: port identifier, if known.
    :param fabric: fabric type label (e.g. ``nvlink`` or ``ib``).
    :return: dimension mapping for the fabric sample.
    """
    dims = _with_dims(identity, source="fabric", fabric=fabric)
    for key, value in (("system", system), ("gpu", gpu), ("port", port)):
        if value:
            dims[key] = str(value)
    return dims


def _gpu_from_chassis(chassis: str) -> Optional[str]:
    """Extract the GPU identifier embedded in a chassis name.

    :param chassis: the chassis identifier.
    :return: the ``GPU_<n>`` identifier, or None when none is present.
    """
    parts = chassis.split("HGX_")
    if len(parts) == 2 and parts[1].startswith("GPU_"):
        return parts[1]
    return chassis if chassis.startswith("GPU_") else None


def _gpu_from_metric_info(info: Mapping[str, str]) -> Optional[str]:
    """Resolve a GPU identifier from parsed MetricProperty fields.

    :param info: parsed MetricProperty fields (gpu, memory, chassis, sensor, …).
    :return: the ``GPU_<n>`` identifier, or None when none can be resolved.
    """
    gpu = str(info.get("gpu") or "")
    if gpu.startswith("GPU_"):
        return gpu
    memory = str(info.get("memory") or "")
    match = re.match(r"(GPU_\d+)", memory)
    if match:
        return match.group(1)
    chassis_gpu = _gpu_from_chassis(str(info.get("chassis") or ""))
    if chassis_gpu:
        return chassis_gpu
    sensor = str(info.get("sensor") or info.get("property") or "")
    match = re.search(r"(?:^|_)(GPU_\d+)(?:_|$)", sensor)
    return match.group(1) if match else None


def _gpu_dim(chassis: str) -> dict[str, str]:
    """Build a ``gpu`` dimension dict from a chassis name.

    :param chassis: the chassis identifier.
    :return: ``{"gpu": <id>}`` when a GPU is present, else an empty dict.
    """
    gpu = _gpu_from_chassis(chassis)
    return {"gpu": gpu} if gpu else {}


def _parse_metric_property(prop: str) -> dict[str, str]:
    """Parse a Redfish MetricProperty URI into its addressing fields.

    :param prop: the MetricProperty path (with optional ``#`` fragment).
    :return: dict with the property name and any system/gpu/port/chassis/index/source context.
    """
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
    if "Sensors" in parts:
        info["metric_source"] = "sensor"
    elif "MemoryMetrics" in parts or "Memory" in parts or "MemorySummary" in parts:
        info["metric_source"] = "memory"
    elif "ProcessorMetrics" in parts:
        info["metric_source"] = "processor"
    for collection, key in (("Systems", "system"), ("Processors", "gpu"),
                            ("Memory", "memory"), ("Ports", "port"),
                            ("Chassis", "chassis"), ("Sensors", "sensor")):
        if collection in parts:
            i = parts.index(collection) + 1
            if i < len(parts):
                info[key] = parts[i]
    return info


def _unit_for_metric(metric: str) -> Optional[str]:
    """Infer a unit annotation from a metric name suffix.

    :param metric: the metric name.
    :return: ``By`` for byte metrics, ``Gbps`` for speed metrics, else None.
    """
    if metric.endswith("_bytes"):
        return "By"
    if metric.endswith("_gbps") or metric.endswith("port_speed"):
        return "Gbps"
    return None


def _generic_metric_name(prop: str) -> str:
    """Vendor-neutral metric name for any MetricReport property not in the
    curated fabric map, so the full telemetry surface is exported rather than
    just fabric counters. e.g. ``FP16ActivityPercent`` -> ``hw.gb300.fp16_activity_percent``.

    :param prop: the MetricReport property name.
    :return: the vendor-neutral ``hw.gb300.*`` metric name.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", prop)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    return f"hw.gb300.{snake or 'metric'}"


def _is_gpu_temperature(prop: str) -> bool:
    """Whether a property name denotes a GPU temperature reading.

    :param prop: the property name to test.
    :return: True if the name refers to a temperature.
    """
    lowered = prop.lower()
    return "temp" in lowered or "temperature" in lowered


def _dim_value(value) -> str:
    """Sanitize a value into a safe, bounded dimension string.

    :param value: the raw dimension value.
    :return: the cleaned value (invalid chars replaced), capped at 256 chars.
    """
    cleaned = DIM_VALUE_OK.sub("_", str(value)).strip("_")
    return (cleaned or "unknown")[:256]


def _escape_label_value(value) -> str:
    """Escape a value for a Prometheus label (backslash, newline, quote).

    :param value: the raw label value.
    :return: the escaped label string.
    """
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    """Format a float as a Prometheus sample value.

    :param value: the numeric sample value.
    :return: an integer string when whole, else the float repr.
    """
    return str(int(value)) if float(value).is_integer() else repr(float(value))
