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
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from ..config import (
    ConfigurationConflict,
    exporter_config_file,
    exporter_credential_file,
)
from . import http_util
from . import identity as identity_mod
from .metric_model import MetricDefinition, MetricSample, _definition

REQUIRED_DIMENSIONS = identity_mod.IDENTITY_DIMENSIONS
build_identity_dimensions = identity_mod.build_identity_dimensions
build_telemetry_identity = identity_mod.build_legacy_gb300_identity
common_sample_dimensions = identity_mod.common_sample_dimensions
parse_dimension_pairs = identity_mod.parse_dimension_pairs
resolve_identity_options = identity_mod.resolve_identity_options
COUNTER_SUFFIXES = (
    "_bytes", "_frames", "_packets", "_errors", "_discards", "_count", "_wait",
)

COUNTER_EXACT = frozenset({"hw.energy_kwh"})
ISO_DURATION = re.compile(
    r"^P"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)
SECRET_ARG_NAMES = {"--password", "--idrac_password", "--idrac-password"}
DIM_VALUE_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
POLL_JITTER_FRACTION = 0.10


_COMMON_DIMS = REQUIRED_DIMENSIONS + ("source",)
_FABRIC_DIMS = _COMMON_DIMS + ("fabric", "system", "gpu", "port", "report")

_STATIC_METRIC_DEFINITIONS = (
    _definition("hw.power", unit="W", description="Power draw in watts."),
    _definition("hw.energy_kwh", "counter", "kWh",
                "Cumulative energy consumed in kilowatt-hours."),
    _definition("hw.temperature", unit="Cel", description="Temperature in Celsius."),
    _definition("hw.voltage", unit="V", description="Voltage reading."),
    _definition("hw.fan_speed", unit="RPM", description="Fan speed in revolutions per minute."),
    _definition("hw.gpu.power", unit="W", description="GPU power draw in watts.", family="gpu"),
    _definition("hw.gpu.temperature", unit="Cel", description="GPU temperature.", family="gpu"),
    _definition("hw.gpu.clock_mhz", unit="MHz", description="GPU operating clock.", family="gpu"),
    _definition("hw.gpu.compute.utilization", unit="%",
                description="GPU compute engine utilization.", family="gpu"),
    _definition("hw.gpu.throttle.duration_seconds", "counter", "s",
                description="GPU throttle duration in seconds.", family="gpu"),
    _definition("hw.gpu.memory.bandwidth_utilization", unit="%",
                description="GPU memory bandwidth utilization.", family="gpu"),
    _definition("hw.gpu.memory.capacity_utilization", unit="%",
                description="GPU memory capacity utilization.", family="gpu"),
    _definition("hw.gpu.memory.clock_mhz", unit="MHz",
                description="GPU memory operating speed.", family="gpu"),
    _definition("hw.gpu.memory.ecc_errors", "counter", None,
                "Cumulative GPU memory ECC error count.", family="gpu"),
    _definition("hw.gpu.memory.row_remap_count", "counter", None,
                "Cumulative GPU memory row-remap count.", family="gpu"),
    _definition("hw.gpu.memory.row_remapping_failed", unit=None,
                description="GPU memory row-remapping failure state.", family="gpu"),
    _definition("hw.component.health", description="One-hot component health state.",
                family="state"),
    _definition("hw.component.health_rollup",
                description="One-hot component aggregate health state.", family="state"),
    _definition("hw.component.state", description="One-hot component enabled state.",
                family="state"),
    _definition("hw.component.last_reset_type",
                description="One-hot component last reset type.", family="state"),
    _definition("hw.power.edp_violation_state",
                description="One-hot EDP violation state.", family="state"),
    _definition("hw.power.break_performance_state",
                description="One-hot power-break performance state.", family="state"),
    _definition("hw.fabric.link_up", description="Fabric link-up state.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_down_reason",
                description="One-hot fabric link-down reason.", family="fabric",
                dimensions=_FABRIC_DIMS + ("reason",)),
    _definition("hw.fabric.port_speed", unit="Gbps",
                description="Fabric port negotiated speed.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.bit_error_rate", description="Fabric bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.effective_ber", description="Fabric effective bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_ber", description="Fabric raw bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_gbps", unit="Gbps",
                description="Fabric receive bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_gbps", unit="Gbps",
                description="Fabric transmit bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_rx_gbps", unit="Gbps",
                description="Fabric raw receive bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_tx_gbps", unit="Gbps",
                description="Fabric raw transmit bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_bytes", "counter", "By",
                "Cumulative fabric receive bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_bytes", "counter", "By",
                "Cumulative fabric transmit bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_tx_bytes", "counter", "By",
                "Cumulative fabric VL15 transmit bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_frames", "counter", None,
                "Cumulative fabric receive frames.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_frames", "counter", None,
                "Cumulative fabric transmit frames.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_tx_packets", "counter", None,
                "Cumulative fabric VL15 transmit packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.crc_errors", "counter", None,
                "Cumulative fabric CRC errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.effective_errors", "counter", None,
                "Cumulative fabric effective errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.fec_errors", "counter", None,
                "Cumulative fabric FEC errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.malformed_packets", "counter", None,
                "Cumulative malformed fabric packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_errors", "counter", None,
                "Cumulative fabric raw errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_errors", "counter", None,
                "Cumulative fabric receive errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_no_protocol_bytes", "counter", "By",
                "Cumulative receive bytes without protocol.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_remote_physical_errors", "counter", None,
                "Cumulative receive remote physical errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_switch_relay_errors", "counter", None,
                "Cumulative receive switch relay errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.symbol_errors", "counter", None,
                "Cumulative fabric symbol errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_discards", "counter", None,
                "Cumulative fabric transmit discards.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_no_protocol_bytes", "counter", "By",
                "Cumulative transmit bytes without protocol.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_wait", "counter", None,
                "Cumulative fabric transmit wait events.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.intentional_link_down_count", "counter", None,
                "Cumulative intentional link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_down_count", "counter", None,
                "Cumulative link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_error_recovery_count", "counter", None,
                "Cumulative link error recovery count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.unintentional_link_down_count", "counter", None,
                "Cumulative unintentional link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_dropped", "counter", None,
                "Cumulative fabric VL15 dropped packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.leak.state", description="Leak detector state.", family="chassis"),
    _definition("hw.fabric.adapter_present", description="Network adapter presence.",
                family="fabric"),
    _definition("hw.component_integrity.enabled",
                description="ComponentIntegrity enabled state.", family="component"),
    _definition("hw.scrape.ok", description="Exporter scrape success.", family="scrape",
                liveness="scrape"),
    _definition("hw.scrape.duration_seconds", unit="s",
                description="Exporter scrape duration.", family="scrape",
                liveness="scrape"),
    _definition("hw.bmc.up",
                description="Per-BMC 0/1 liveness gauge (1 reachable, 0 unreachable).",
                family="scrape", liveness="scrape"),
    _definition(
        "redfish_exporter_scrape_success",
        description="Whether the latest scrape completed without a supported collector failure.",
        family="exporter",
        dimensions=_COMMON_DIMS,
        liveness="scrape",
    ),
    _definition(
        "redfish_exporter_scrape_partial",
        description="Whether the latest scrape returned partial telemetry.",
        family="exporter",
        dimensions=_COMMON_DIMS,
        liveness="scrape",
    ),
    _definition(
        "redfish_exporter_scrape_duration_seconds",
        unit="s",
        description="Duration of the latest exporter scrape.",
        family="exporter",
        dimensions=_COMMON_DIMS,
        liveness="scrape",
    ),
    _definition(
        "redfish_exporter_last_success_timestamp_seconds",
        unit="s",
        description="Unix timestamp of the latest successful exporter scrape.",
        family="exporter",
        dimensions=_COMMON_DIMS,
        liveness="scrape",
    ),
    _definition(
        "redfish_exporter_collector_success",
        description="Whether a telemetry collector completed successfully.",
        family="exporter",
        dimensions=_COMMON_DIMS + ("collector",),
    ),
    _definition(
        "redfish_exporter_collector_supported",
        description="Whether the BMC supports a telemetry collector.",
        family="exporter",
        dimensions=_COMMON_DIMS + ("collector",),
    ),
    _definition(
        "redfish_exporter_collector_duration_seconds",
        unit="s",
        description="Duration of one telemetry collector.",
        family="exporter",
        dimensions=_COMMON_DIMS + ("collector",),
    ),
    _definition(
        "redfish_exporter_collector_samples",
        description="Number of samples returned by one telemetry collector.",
        family="exporter",
        dimensions=_COMMON_DIMS + ("collector",),
    ),
    _definition(
        "redfish_exporter_collection_errors_total",
        kind="counter",
        description="Cumulative telemetry collection errors.",
        family="exporter",
        dimensions=_COMMON_DIMS + ("collector", "error"),
    ),
)

METRIC_DEFINITIONS = {definition.name: definition
                      for definition in _STATIC_METRIC_DEFINITIONS}


def metric_definitions() -> Mapping[str, MetricDefinition]:
    """Return the static metric-definition catalog by metric name.

    :return: mapping of metric name to static MetricDefinition.
    """
    return METRIC_DEFINITIONS


def metric_definition(metric_name: str) -> MetricDefinition:
    """Return the catalog definition for ``metric_name``.

    Curated ``hw.*`` metrics are registered statically. The GB300 MetricReport
    catch-all family is generated deterministically from the metric name so new
    firmware properties keep exporting without adding free-form backend logic.

    :param metric_name: exported metric name.
    :return: static or generated MetricDefinition.
    :raises KeyError: when the metric is not registered and is not in a dynamic family.
    """
    if metric_name in METRIC_DEFINITIONS:
        return METRIC_DEFINITIONS[metric_name]
    if metric_name.startswith("hw.gb300."):
        return MetricDefinition(
            name=metric_name,
            kind=_infer_metric_kind(metric_name),
            unit=_infer_metric_unit(metric_name),
            description="GB300 MetricReport numeric property.",
            family="gb300",
            dimensions=_COMMON_DIMS + ("property", "system", "gpu", "port",
                                       "chassis", "index", "report"),
        )
    raise KeyError(f"metric {metric_name!r} is not registered in the catalog")


def _infer_metric_kind(metric_name: str) -> str:
    """Infer the metric kind for generated metric families.

    :param metric_name: generated metric name.
    :return: ``counter`` for monotonic totals, else ``gauge``.
    """
    if metric_name in COUNTER_EXACT:
        return "counter"
    if any(metric_name.endswith(suffix) for suffix in COUNTER_SUFFIXES):
        return "counter"
    return "gauge"


def _infer_metric_unit(metric_name: str) -> Optional[str]:
    """Infer a unit annotation for generated metric families.

    :param metric_name: generated metric name.
    :return: inferred unit string, or None when dimensionless or unknown.
    """
    lowered = metric_name.lower()
    if lowered.endswith("_bytes"):
        return "By"
    if lowered.endswith("_gbps") or lowered.endswith("port_speed"):
        return "Gbps"
    if lowered.endswith("_mhz") or "_freq_" in lowered or "frequency" in lowered:
        return "MHz"
    if lowered.endswith("_seconds"):
        return "s"
    if lowered.endswith("_kwh") or "energy" in lowered:
        return "kWh"
    if "temp" in lowered or "temperature" in lowered:
        return "Cel"
    if "power" in lowered:
        return "W"
    if "voltage" in lowered:
        return "V"
    if "percent" in lowered or "utilization" in lowered:
        return "%"
    return None


@dataclass(frozen=True)
class CollectorResult:
    """Outcome from one read-only telemetry collector."""

    name: str
    supported: bool
    success: bool
    duration_seconds: float
    rows: tuple[Mapping, ...] = ()
    error_kind: Optional[str] = None


# Credential-file keys the exporter honors. REDFISH_* is the going-forward set;
# the legacy IDRAC_* keys still work as a fallback during the rename.
_EXPORTER_CRED_KEYS = frozenset({
    "REDFISH_IP", "REDFISH_USERNAME", "REDFISH_PASSWORD", "REDFISH_PORT",
    "IDRAC_IP", "IDRAC_USERNAME", "IDRAC_PASSWORD", "IDRAC_PORT",
})
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


def _config_path(path: Optional[str] = None) -> Optional[str]:
    """Return the explicit or environment-provided exporter config path.

    :param path: explicit config path.
    :return: config path from argument or environment, or None.
    """
    return _first_non_empty(path, exporter_config_file())


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
        "deployment_environment": _config_value(
            config, "identity", "deployment_environment", "deployment_environment"),
        "deployment_environment_compat": _config_value(
            config, "identity", "deployment_environment_compat",
            "deployment_environment_compat"),
        "require_deployment_environment": _config_value(
            config, "identity", "require_deployment_environment",
            "require_deployment_environment"),
        "service_name": _config_value(
            config, "identity", "service_name", "service_name"),
        "service_namespace": _config_value(
            config, "identity", "service_namespace", "service_namespace"),
        "service_instance_id": _config_value(
            config, "identity", "service_instance_id", "service_instance_id"),
        "service_version": _config_value(
            config, "identity", "service_version", "service_version"),
        "service_criticality": _config_value(
            config, "identity", "service_criticality", "service_criticality"),
        "extra_dimensions": _first_non_empty(
            _config_value(config, "identity", "extra_dimensions", "extra_dimensions"),
            config.get("dimensions"),
            config.get("extra_dimensions"),
        ),
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
    file_path = file_path or exporter_credential_file()
    if not file_path:
        return
    values = load_exporter_env_file(file_path)
    # (canonical namespace attr, legacy namespace attr, REDFISH_* key, legacy
    # IDRAC_* key). Matching pairs are harmless; conflicting pairs fail closed.
    # Legacy attrs keep older programmatic callers working.
    mapping = (
        ("redfish_host", "idrac_ip", "REDFISH_IP", "IDRAC_IP"),
        ("redfish_username", "idrac_username", "REDFISH_USERNAME", "IDRAC_USERNAME"),
        ("redfish_password", "idrac_password", "REDFISH_PASSWORD", "IDRAC_PASSWORD"),
        ("redfish_port", "idrac_port", "REDFISH_PORT", "IDRAC_PORT"),
    )
    for primary_attr, legacy_attr, redfish_key, idrac_key in mapping:
        if (
            redfish_key in values
            and idrac_key in values
            and values[redfish_key].strip() != values[idrac_key].strip()
        ):
            raise ConfigurationConflict(
                f"Configuration conflict in {file_path}: "
                f"use only {redfish_key} for this credential.")
        attrs = [
            attr
            for attr in (primary_attr, legacy_attr)
            if hasattr(args, attr)
        ]
        if not attrs:
            continue
        attr = primary_attr if primary_attr in attrs else legacy_attr
        key = redfish_key if redfish_key in values else idrac_key
        if key not in values:
            continue
        is_password = attr in {"redfish_password", "idrac_password"}
        current = getattr(args, attr, "")
        if current in ("", None, "root") or is_password:
            value = values[key]
            is_port = attr in {"redfish_port", "idrac_port"}
            value = int(value) if is_port else value
            for target_attr in attrs:
                setattr(args, target_attr, value)


def scrape_health_samples(
        identity: Mapping[str, str],
        ok: bool,
        duration_seconds: float,
        collector_results: Iterable[CollectorResult] = (),
        partial: bool = False,
        timestamp_seconds: Optional[float] = None) -> list[MetricSample]:
    """Return per-scrape liveness and duration samples.

    Includes ``hw.bmc.up`` — a per-BMC 0/1 liveness gauge carrying the full
    exporter identity dimensions, emitted every scrape cycle: 1 when the BMC
    scrape succeeds and 0 when it fails, so an unreachable BMC is distinguishable
    from missing telemetry (issue #402).

    :param identity: fixed join dimensions applied to the health samples.
    :param ok: whether the scrape succeeded (1.0) or failed (0.0).
    :param duration_seconds: scrape wall-clock duration, in seconds.
    :param collector_results: per-collector outcomes for exporter self-telemetry.
    :param partial: whether at least one supported collector failed while another
        collector still returned a usable result.
    :param timestamp_seconds: wall-clock timestamp used for last-success freshness.
    :return: scrape-level, collector-level, and deprecated compatibility samples.
    """
    dims = _with_dims(identity, source="exporter")
    duration = _as_float(duration_seconds)
    safe_duration = max(0.0, duration if duration is not None else 0.0)
    health = [
        _sample("redfish_exporter_scrape_success", 1.0 if ok else 0.0, dims, None),
        _sample(
            "redfish_exporter_scrape_partial",
            1.0 if partial else 0.0,
            dims,
            None,
        ),
        _sample("redfish_exporter_scrape_duration_seconds", safe_duration, dims, "s"),
        _sample(
            "redfish_exporter_last_success_timestamp_seconds",
            float(timestamp_seconds or 0.0) if ok else 0.0,
            dims,
            "s",
        ),
        _sample("hw.scrape.ok", 1.0 if ok else 0.0, dims, None),
        # hw.bmc.up is a per-BMC 0/1 liveness gauge (Prometheus-style ``up``),
        # emitted on EVERY scrape cycle regardless of outcome: 1 when the BMC
        # scrape succeeds, 0 when it fails (connection error, timeout, auth
        # failure, or a non-2xx ServiceRoot). Because it is emitted with the
        # full exporter identity dimensions even on failure, a dashboard can
        # tell an UNREACHABLE BMC (hw.bmc.up=0) apart from MISSING telemetry
        # (no series at all). See specs/telemetry/expected_signals.yaml and
        # gates.md; issue #402.
        _sample("hw.bmc.up", 1.0 if ok else 0.0, dims, None),
        _sample(
            "hw.scrape.duration_seconds",
            safe_duration,
            dims,
            "s",
        ),
    ]
    for result in collector_results:
        collector_dims = _with_dims(
            identity,
            source="exporter",
            collector=_dim_value(result.name),
        )
        collector_duration = _as_float(result.duration_seconds)
        collector_duration = max(
            0.0,
            collector_duration if collector_duration is not None else 0.0,
        )
        health.extend([
            _sample(
                "redfish_exporter_collector_success",
                1.0 if result.success else 0.0,
                collector_dims,
                None,
            ),
            _sample(
                "redfish_exporter_collector_supported",
                1.0 if result.supported else 0.0,
                collector_dims,
                None,
            ),
            _sample(
                "redfish_exporter_collector_duration_seconds",
                collector_duration,
                collector_dims,
                "s",
            ),
            _sample(
                "redfish_exporter_collector_samples",
                float(len(result.rows)),
                collector_dims,
                None,
            ),
        ])
        if result.error_kind:
            error_dims = collector_dims | {"error": _dim_value(result.error_kind)}
            health.append(_sample(
                "redfish_exporter_collection_errors_total",
                1.0,
                error_dims,
                None,
                metric_type="counter",
            ))
    return health


def collector_scrape_status(results: Iterable[CollectorResult]) -> tuple[bool, bool]:
    """Return ``(success, partial)`` for a set of collector outcomes.

    :param results: per-collector outcomes from one scrape.
    :return: success is true only when no supported collector failed; partial is true
        when failures and usable collector results are both present.
    """
    materialized = tuple(results)
    failed_supported = [
        result for result in materialized
        if result.supported and not result.success
    ]
    any_usable = any(result.success for result in materialized)
    return not failed_supported, bool(failed_supported and any_usable)


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


def _reading(field):
    """Return the ``Reading`` of a mapping field, or the field itself.

    :param field: a Redfish reading value or ``{"Reading": …}`` mapping.
    :return: the scalar reading value.
    """
    if isinstance(field, Mapping):
        return field.get("Reading")
    return field


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


def _sample(metric: str,
            value: float,
            dims: Mapping[str, str],
            unit: Optional[str] = None,
            timestamp: Optional[str] = None,
            metric_type: Optional[str] = None) -> MetricSample:
    """Construct a MetricSample with stringified dimension values.

    :param metric: metric name.
    :param value: numeric sample value.
    :param dims: dimension mapping.
    :param unit: source-provided unit annotation; the catalog's canonical unit wins.
    :param timestamp: optional sample timestamp.
    :param metric_type: optional caller classification, validated against the catalog.
    :return: the assembled MetricSample.
    """
    definition = metric_definition(metric)
    if metric_type is not None and metric_type != definition.kind:
        raise ValueError(
            f"{metric} type {metric_type!r} does not match catalog type "
            f"{definition.kind!r}")
    return MetricSample(metric=metric, value=float(value),
                        dimensions={k: str(v) for k, v in dims.items()},
                        metric_type=definition.kind,
                        unit=definition.unit, timestamp=timestamp)


def _with_dims(identity: Mapping[str, str], **extra) -> dict[str, str]:
    """Build a dimension dict from identity plus non-empty extras.

    :param identity: fixed join dimensions to seed the result.
    :return: dimension mapping with the required dims plus any non-empty extras.
    """
    dims = {
        str(key): str(value)
        for key, value in identity.items()
        if value not in (None, "")
    }
    for key in REQUIRED_DIMENSIONS:
        dims.setdefault(key, str(identity.get(key) or "unknown"))
    for key, value in extra.items():
        if value not in (None, ""):
            dims[key] = str(value)
    return dims


def _unit_for_metric(metric: str) -> Optional[str]:
    """Return the catalog unit annotation for a metric.

    :param metric: the metric name.
    :return: configured unit or inferred dynamic-family unit.
    """
    return metric_definition(metric).unit


def _dim_value(value) -> str:
    """Sanitize a value into a safe, bounded dimension string.

    :param value: the raw dimension value.
    :return: the cleaned value (invalid chars replaced), capped at 256 chars.
    """
    cleaned = DIM_VALUE_OK.sub("_", str(value)).strip("_")
    return (cleaned or "unknown")[:256]


