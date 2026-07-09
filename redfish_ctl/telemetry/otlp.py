"""Native OTLP (OpenTelemetry) output for the telemetry exporter.

This emits the SAME ``hw.*`` metric contract as the Prometheus/SignalFx paths,
mapped onto the OTLP data model so ``redfish_ctl`` drops into an existing
OpenTelemetry pipeline as just another producer:

* identity dimensions (``host.name``/``server.address``/``bmc.ip``/``node``/
  ``vendor``) become OTel **resource** attributes; the remaining per-metric
  dimensions (``gpu``/``port``/``chassis``/``system``/``index``) become
  **datapoint** attributes;
* monotonic cumulative counters (fabric byte/frame/error/packet/discard/count
  totals and ``hw.energy_kwh``) become OTLP **Sum**; everything instantaneous
  (power, temperature, rates, ratios, booleans) stays a **Gauge**.

Metric names and dimension keys are unchanged, so this does not alter the
contract the Prometheus/SignalFx outputs already emit. The OpenTelemetry SDK is
imported lazily and is only required when ``--output otlp`` is used
(``pip install "redfish_ctl[otlp]"``).
"""
from __future__ import annotations

import os
import time
from typing import Callable, Iterable, Optional

# Identity dims that describe the emitting host -> OTel resource attributes.
RESOURCE_DIM_KEYS = ("host.name", "server.address", "bmc.ip", "node", "vendor")

# A metric is a monotonic cumulative counter (OTLP Sum) when its name ends with
# one of these suffixes, or is total energy. Everything else is a Gauge.
_COUNTER_SUFFIXES = (
    "_bytes", "_frames", "_packets", "_errors", "_discards", "_count", "_wait",
)
_COUNTER_EXACT = frozenset({"hw.energy_kwh"})

_MISSING_SDK_MSG = (
    "native OTLP output needs the OpenTelemetry SDK. Install it with:\n"
    '    pip install "redfish_ctl[otlp]"'
)


def is_monotonic_counter(metric_name: str) -> bool:
    """True when ``metric_name`` is a cumulative counter that should be an OTLP Sum."""
    if metric_name in _COUNTER_EXACT:
        return True
    return any(metric_name.endswith(sfx) for sfx in _COUNTER_SUFFIXES)


def resolve_otlp_config(endpoint: Optional[str] = None,
                        protocol: Optional[str] = None,
                        headers: Optional[str] = None) -> tuple[Optional[str], str, Optional[str]]:
    """Resolve endpoint/protocol/headers from explicit args, then standard OTEL_* env.

    Metric-signal-specific vars win over the generic ones, matching the OTel spec
    so redfish_ctl behaves like every other OTLP producer in the pipeline.
    """
    endpoint = (endpoint
                or os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
                or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    protocol = (protocol
                or os.environ.get("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL")
                or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
                or "grpc")
    headers = (headers
               or os.environ.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS")
               or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"))
    return endpoint, protocol, headers


def _resource_attrs(samples, service_name: str) -> dict:
    """Pull the shared identity dims off the samples for the OTel Resource."""
    attrs = {"service.name": service_name}
    for sample in samples:
        for key in RESOURCE_DIM_KEYS:
            if key in sample.dimensions and key not in attrs:
                attrs[key] = sample.dimensions[key]
    return attrs


def metrics_data_from_samples(samples: Iterable, service_name: str = "redfish_ctl",
                              timestamp_ns: Optional[int] = None):
    """Build an OTLP ``MetricsData`` from exporter ``MetricSample``s (lazy SDK import)."""
    try:
        from opentelemetry.sdk.metrics.export import (
            AggregationTemporality,
            Gauge,
            Metric,
            MetricsData,
            NumberDataPoint,
            ResourceMetrics,
            ScopeMetrics,
            Sum,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    except ImportError as exc:  # pragma: no cover - exercised via the CLI path
        raise RuntimeError(_MISSING_SDK_MSG) from exc

    samples = list(samples)
    ts = timestamp_ns if timestamp_ns is not None else time.time_ns()

    grouped: dict[str, dict] = {}
    for sample in samples:
        dp_attrs = {k: v for k, v in sample.dimensions.items() if k not in RESOURCE_DIM_KEYS}
        entry = grouped.setdefault(sample.metric, {"unit": sample.unit, "points": []})
        entry["points"].append(NumberDataPoint(
            attributes=dp_attrs,
            start_time_unix_nano=ts,
            time_unix_nano=ts,
            value=sample.value,
        ))

    metrics = []
    for name, entry in grouped.items():
        if is_monotonic_counter(name):
            data = Sum(
                data_points=entry["points"],
                aggregation_temporality=AggregationTemporality.CUMULATIVE,
                is_monotonic=True,
            )
        else:
            data = Gauge(data_points=entry["points"])
        metrics.append(Metric(name=name, description="", unit=entry["unit"] or "", data=data))

    resource = Resource.create(_resource_attrs(samples, service_name))
    scope_metrics = ScopeMetrics(
        scope=InstrumentationScope(name="redfish_ctl.telemetry"),
        metrics=metrics,
        schema_url="",
    )
    return MetricsData(resource_metrics=[ResourceMetrics(
        resource=resource, scope_metrics=[scope_metrics], schema_url="")])


def _build_exporter(endpoint: Optional[str], protocol: str, headers: Optional[str]):
    """Construct the grpc or http OTLP metric exporter (lazy import)."""
    kwargs: dict = {}
    if endpoint:
        kwargs["endpoint"] = endpoint
    if headers:
        kwargs["headers"] = headers
    try:
        if str(protocol).startswith("http"):
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
        else:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
    except ImportError as exc:  # pragma: no cover - exercised via the CLI path
        raise RuntimeError(_MISSING_SDK_MSG) from exc
    return OTLPMetricExporter(**kwargs)


def push_otlp(samples: Iterable, service_name: str = "redfish_ctl",
              endpoint: Optional[str] = None, protocol: Optional[str] = None,
              headers: Optional[str] = None):
    """Build OTLP metrics from samples and export them once. Returns the export result."""
    endpoint, protocol, headers = resolve_otlp_config(endpoint, protocol, headers)
    metrics_data = metrics_data_from_samples(samples, service_name)
    exporter = _build_exporter(endpoint, protocol, headers)
    try:
        return exporter.export(metrics_data)
    finally:
        exporter.shutdown()


def run_otlp_loop(scrape_samples: Callable[[], Iterable], interval: float,
                  service_name: str = "redfish_ctl", endpoint: Optional[str] = None,
                  protocol: Optional[str] = None, headers: Optional[str] = None,
                  sleep: Callable[[float], None] = time.sleep) -> None:  # pragma: no cover
    """Scrape and push OTLP on a fixed interval until interrupted."""
    endpoint, protocol, headers = resolve_otlp_config(endpoint, protocol, headers)
    exporter = _build_exporter(endpoint, protocol, headers)
    try:
        while True:
            try:
                exporter.export(metrics_data_from_samples(scrape_samples(), service_name))
            except Exception:  # keep the poller alive across transient export/scrape errors
                pass
            sleep(max(1.0, float(interval)))
    finally:
        exporter.shutdown()
