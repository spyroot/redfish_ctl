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

# Identity dims that describe the emitting host/deployment/service -> OTel resource
# attributes. service.name and deployment.environment[.name] are emitted as dimensions on
# the SignalFx/Prometheus planes, but on OTLP they are process-scoped Resource attributes
# (lifted here, and stripped from datapoint attributes below to avoid a double-emit).
RESOURCE_DIM_KEYS = ("host.name", "server.address", "bmc.ip", "node", "vendor",
                     "service.name", "deployment.environment", "deployment.environment.name")
# The unresolved deployment sentinel: OTel defines no "unknown environment" convention
# and a Resource attribute is a factual claim, so an unknown value is omitted from the
# OTLP Resource entirely (the SignalFx/Prometheus join planes still carry it for #363).
_DEPLOYMENT_ENVIRONMENT_UNKNOWN = "unknown"

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
    """True when ``metric_name`` is a cumulative counter that should be an OTLP Sum.

    :param metric_name: the metric name to classify.
    :return: True for a monotonic cumulative counter, False for a gauge.
    """
    if metric_name in _COUNTER_EXACT:
        return True
    return any(metric_name.endswith(sfx) for sfx in _COUNTER_SUFFIXES)


def resolve_otlp_config(endpoint: Optional[str] = None,
                        protocol: Optional[str] = None,
                        headers: Optional[str] = None) -> tuple[Optional[str], str, Optional[str]]:
    """Resolve endpoint/protocol/headers from explicit args, then standard OTEL_* env.

    Metric-signal-specific vars win over the generic ones, matching the OTel spec
    so redfish_ctl behaves like every other OTLP producer in the pipeline.

    :param endpoint: explicit OTLP endpoint; falls back to OTEL_* env when None.
    :param protocol: explicit OTLP transport; falls back to OTEL_* env, else ``grpc``.
    :param headers: explicit OTLP headers; falls back to OTEL_* env when None.
    :return: tuple of (endpoint, protocol, headers) after resolution.
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
    """Pull the shared identity dims off the samples for the OTel Resource.

    :param samples: iterable of MetricSample objects to read identity dims from.
    :param service_name: value for the ``service.name`` resource attribute.
    :return: dict of OTel resource attributes.
    """
    attrs = {"service.name": service_name}
    for sample in samples:
        for key in RESOURCE_DIM_KEYS:
            if key in sample.dimensions and key not in attrs:
                value = sample.dimensions[key]
                if (key.startswith("deployment.environment")
                        and value == _DEPLOYMENT_ENVIRONMENT_UNKNOWN):
                    continue  # do not assert an unknown environment on the Resource
                attrs[key] = value
    return attrs


def metrics_data_from_samples(samples: Iterable, service_name: str = "redfish_ctl",
                              timestamp_ns: Optional[int] = None):
    """Build an OTLP ``MetricsData`` from exporter ``MetricSample``s (lazy SDK import).

    :param samples: iterable of exporter MetricSample objects.
    :param service_name: value for the ``service.name`` resource attribute.
    :param timestamp_ns: unix nanosecond timestamp for every datapoint; ``time.time_ns()``
        when None.
    :return: an OTLP ``MetricsData`` grouping the samples into Sum/Gauge metrics.
    :raises RuntimeError: when the OpenTelemetry SDK is not installed.
    """
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
    """Construct the grpc or http OTLP metric exporter (lazy import).

    :param endpoint: OTLP endpoint passed to the exporter; omitted when None.
    :param protocol: transport selector; ``http*`` picks the HTTP exporter, else grpc.
    :param headers: OTLP headers passed to the exporter; omitted when None.
    :return: a configured OTLPMetricExporter instance.
    :raises RuntimeError: when the OpenTelemetry SDK/exporter is not installed.
    """
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
    """Build OTLP metrics from samples and export them once. Returns the export result.

    :param samples: iterable of exporter MetricSample objects to export.
    :param service_name: value for the ``service.name`` resource attribute.
    :param endpoint: OTLP endpoint; resolved from OTEL_* env when None.
    :param protocol: OTLP transport; resolved from OTEL_* env, else ``grpc``.
    :param headers: OTLP headers; resolved from OTEL_* env when None.
    :return: the exporter's ``MetricExportResult`` from the single export call.
    """
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
    """Scrape and push OTLP on a fixed interval until interrupted.

    :param scrape_samples: callable returning a fresh iterable of MetricSample per scrape.
    :param interval: seconds between scrapes; clamped to a minimum of 1 second.
    :param service_name: value for the ``service.name`` resource attribute.
    :param endpoint: OTLP endpoint; resolved from OTEL_* env when None.
    :param protocol: OTLP transport; resolved from OTEL_* env, else ``grpc``.
    :param headers: OTLP headers; resolved from OTEL_* env when None.
    :param sleep: sleep function between scrapes (injectable for testing).
    """
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
