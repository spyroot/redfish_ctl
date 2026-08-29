"""Shared exporter config, identity, sample, and scrape-health helpers."""

from __future__ import annotations

import json
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from ..config import exporter_config_file, exporter_credential_file
from . import identity as identity_mod
from .metric_model import MetricDefinition, MetricSample, _definition

REQUIRED_DIMENSIONS = identity_mod.IDENTITY_DIMENSIONS
build_identity_dimensions = identity_mod.build_identity_dimensions
common_sample_dimensions = identity_mod.common_sample_dimensions
parse_dimension_pairs = identity_mod.parse_dimension_pairs
resolve_identity_options = identity_mod.resolve_identity_options
ISO_DURATION = re.compile(
    r"^P"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)
SECRET_ARG_NAMES = {"--password"}
DIM_VALUE_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
POLL_JITTER_FRACTION = 0.10


_COMMON_DIMS = REQUIRED_DIMENSIONS + ("source",)

_STATIC_METRIC_DEFINITIONS = (
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
    """Return a shared exporter self-metric definition.

    :param metric_name: exported metric name.
    :return: shared MetricDefinition.
    :raises KeyError: when the metric belongs to a concrete vendor reader.
    """
    if metric_name in METRIC_DEFINITIONS:
        return METRIC_DEFINITIONS[metric_name]
    raise KeyError(f"metric {metric_name!r} is not a shared exporter metric")


@dataclass(frozen=True)
class CollectorResult:
    """Outcome from one read-only telemetry collector."""

    name: str
    supported: bool
    success: bool
    duration_seconds: float
    rows: tuple[Mapping, ...] = ()
    error_kind: Optional[str] = None


_EXPORTER_CRED_KEYS = frozenset({
    "REDFISH_IP", "REDFISH_USERNAME", "REDFISH_PASSWORD", "REDFISH_PORT",
})
def load_exporter_env_file(path: os.PathLike[str] | str) -> dict[str, str]:
    """Read a simple KEY=VALUE runtime env file without printing secret values.

    Accepts the canonical REDFISH_IP/USERNAME/PASSWORD/PORT keys.

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
    mapping = (
        ("redfish_host", "REDFISH_IP"),
        ("redfish_username", "REDFISH_USERNAME"),
        ("redfish_password", "REDFISH_PASSWORD"),
        ("redfish_port", "REDFISH_PORT"),
    )
    for attr, key in mapping:
        if not hasattr(args, attr):
            continue
        if key not in values:
            continue
        is_password = attr == "redfish_password"
        current = getattr(args, attr, "")
        if current in ("", None, "root") or is_password:
            value = values[key]
            is_port = attr == "redfish_port"
            value = int(value) if is_port else value
            setattr(args, attr, value)


def scrape_health_samples(
        identity: Mapping[str, str],
        ok: bool,
        duration_seconds: float,
        collector_results: Iterable[CollectorResult] = (),
        partial: bool = False,
        timestamp_seconds: Optional[float] = None,
        collection_error_totals: Optional[Mapping[tuple[str, str], float]] = None,
        ) -> list[MetricSample]:
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
    :param timestamp_seconds: wall-clock timestamp of the latest successful scrape.
    :param collection_error_totals: cumulative error counts keyed by
        ``(collector, error_kind)``; when omitted, current errors emit as 1.
    :return: scrape-level, collector-level, and deprecated compatibility samples.
    """
    dims = _with_dims(identity, source="exporter")
    collector_results = tuple(collector_results)
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
            float(timestamp_seconds or 0.0),
            dims,
            "s",
        ),
        _sample("hw.scrape.ok", 1.0 if ok else 0.0, dims, None),
        # Always emit hw.bmc.up so failed scrapes differ from absent series.
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
    if collection_error_totals is None:
        error_totals = {
            (result.name, result.error_kind): 1.0
            for result in collector_results
            if result.error_kind
        }
    else:
        error_totals = collection_error_totals
    for (collector, error_kind), total in sorted(error_totals.items()):
        error_dims = _with_dims(
            identity,
            source="exporter",
            collector=_dim_value(collector),
            error=_dim_value(error_kind),
        )
        health.append(_sample(
            "redfish_exporter_collection_errors_total",
            float(total),
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
    any_usable = any(
        result.supported and result.success for result in materialized
    )
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
            metric_type: Optional[str] = None,
            definition_lookup: Callable[[str], MetricDefinition] = metric_definition,
            ) -> MetricSample:
    """Construct a MetricSample with stringified dimension values.

    :param metric: metric name.
    :param value: numeric sample value.
    :param dims: dimension mapping.
    :param unit: source-provided unit annotation; the catalog's canonical unit wins.
    :param timestamp: optional sample timestamp.
    :param metric_type: optional caller classification, validated against the catalog.
    :param definition_lookup: catalog resolver owned by the shared runtime or a
        concrete vendor reader.
    :return: the assembled MetricSample.
    """
    definition = definition_lookup(metric)
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


def _dim_value(value) -> str:
    """Sanitize a value into a safe, bounded dimension string.

    :param value: the raw dimension value.
    :return: the cleaned value (invalid chars replaced), capped at 256 chars.
    """
    cleaned = DIM_VALUE_OK.sub("_", str(value)).strip("_")
    return (cleaned or "unknown")[:256]
