"""Native OTLP (OpenTelemetry) output for the telemetry exporter.

This maps reader-produced ``MetricSample`` objects onto the OTLP data model:

* identity dimensions (``host.name``/``server.address``/``bmc.ip``/``node``/
  ``vendor``/``deployment.environment``/``deployment.environment.name``)
  become OTel **resource** attributes when present; the remaining per-metric
  dimensions (``gpu``/``port``/``chassis``/``system``/``index``) become
  **datapoint** attributes;
* samples declared as counters become monotonic OTLP **Sum** values; other
  samples become **Gauge** values.

Metric names and dimension keys are unchanged. The OpenTelemetry SDK is imported
lazily and is only required when ``--output otlp`` is used
(``pip install "redfish_ctl[otlp]"``).
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from ...config import otlp_protocol
from ..identity import RESOURCE_DIMENSIONS

# Identity dims that describe the emitting host -> OTel resource attributes.
RESOURCE_DIM_KEYS = RESOURCE_DIMENSIONS

_MISSING_SDK_MSG = (
    "native OTLP output needs the OpenTelemetry SDK. Install it with:\n"
    '    pip install "redfish_ctl[otlp]"'
)


def resolve_otlp_config(endpoint: Optional[str] = None,
                        protocol: Optional[str] = None,
                        headers: Optional[str] = None) -> tuple[Optional[str], str, Optional[str]]:
    """Resolve endpoint/protocol/headers, protocol via config, endpoint/headers passthrough.

    Only the transport ``protocol`` is interpreted here: an explicit arg wins,
    else :func:`config.otlp_protocol` (metric-signal vars overriding the generic
    ones per the OTel spec), else ``grpc``. Endpoint and headers are passthrough:
    when no explicit arg is given they stay None so the OpenTelemetry SDK reads
    ``OTEL_EXPORTER_OTLP_[METRICS_]ENDPOINT``/``HEADERS`` itself (it already
    applies the metrics>generic precedence).

    :param endpoint: explicit OTLP endpoint; stays None for SDK passthrough when None.
    :param protocol: explicit OTLP transport; falls back to config, else ``grpc``.
    :param headers: explicit OTLP headers; stays None for SDK passthrough when None.
    :return: tuple of (endpoint, protocol, headers) after resolution.
    """
    protocol = protocol or otlp_protocol() or "grpc"
    return endpoint, protocol, headers


def _resource_attrs(samples, service_name: str) -> dict:
    """Pull the shared identity dims off the samples for the OTel Resource.

    :param samples: iterable of MetricSample objects to read identity dims from.
    :param service_name: value for the ``service.name`` resource attribute.
    :return: dict of OTel resource attributes.
    """
    attrs: dict = {}
    for sample in samples:
        for key in RESOURCE_DIM_KEYS:
            if key in sample.dimensions and key not in attrs:
                attrs[key] = sample.dimensions[key]
    # service.name rides the samples' dimensions now; the param is only a fallback
    # for samples that predate the identity carrying it.
    attrs.setdefault("service.name", service_name)
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
        entry = grouped.setdefault(
            sample.metric,
            {"unit": sample.unit, "metric_type": sample.metric_type, "points": []})
        entry["points"].append(NumberDataPoint(
            attributes=dp_attrs,
            start_time_unix_nano=ts,
            time_unix_nano=ts,
            value=sample.value,
        ))

    metrics = []
    for name, entry in grouped.items():
        # Trust the sample's declared metric_type (the single classifier, set at
        # _sample construction) so OTLP agrees with Prometheus/SignalFx instead of
        # re-deriving from the name.
        if entry["metric_type"] == "counter":
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
    :param endpoint: concrete OTLP endpoint, already resolved by the caller
        (e.g. :class:`OtlpWriter`); the OTLP SDK applies its own OTEL_* default
        when None. Use :func:`resolve_otlp_config` to resolve before calling.
    :param protocol: concrete OTLP transport; ``grpc`` when None.
    :param headers: concrete OTLP headers, or None.
    :return: the exporter's ``MetricExportResult`` from the single export call.
    """
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
    :param endpoint: concrete OTLP endpoint, already resolved by the caller
        (e.g. :class:`OtlpWriter`); the OTLP SDK applies its own OTEL_* default
        when None.
    :param protocol: concrete OTLP transport; ``grpc`` when None.
    :param headers: concrete OTLP headers, or None.
    :param sleep: sleep function between scrapes (injectable for testing).
    """
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
