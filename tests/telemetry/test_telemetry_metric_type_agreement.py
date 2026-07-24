"""GATE: metric type agrees across Prometheus, SignalFx, and OTLP for every metric.

Regression guard: previously counters were gauges on Prometheus/SignalFx but monotonic Sums
on OTLP (the same metric had two types depending on backend). This gate fails if a metric's
gauge/counter classification ever diverges between the three backends again, or drifts from
its expected type.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.telemetry import exporter, otlp

# Curated metric -> expected type, including the tricky cases the data-type audit surfaced.
_EXPECTED = {
    # gauges (instantaneous)
    "hw.power": "gauge",
    "hw.temperature": "gauge",
    "hw.voltage": "gauge",
    "hw.gpu.power": "gauge",
    "hw.gpu.temperature": "gauge",
    "hw.scrape.duration_seconds": "gauge",
    "redfish_exporter_scrape_duration_seconds": "gauge",
    "redfish_exporter_last_success_timestamp_seconds": "gauge",
    # counters (monotonic cumulative)
    "hw.fabric.rx_bytes": "counter",
    "hw.fabric.tx_bytes": "counter",
    "hw.fabric.crc_errors": "counter",
    "hw.fabric.link_down_count": "counter",
    "hw.fabric.vl15_dropped": "counter",
    "hw.energy_kwh": "counter",
    "redfish_exporter_collection_errors_total": "counter",
    "hw.gpu.throttle.duration_seconds": "counter",
}

_DIMS = {"host.name": "h", "node": "n", "server.address": "s", "bmc.ip": "b", "vendor": "v"}
# hw.gpu.throttle.duration_seconds is cumulative but its name does not suffix-match, so its
# mapper sets metric_type explicitly; everything else derives from the name.
_EXPLICIT = {"hw.gpu.throttle.duration_seconds": "counter"}


def _prometheus_type(name, sample):
    """Return the Prometheus ``# TYPE`` for a metric, or None.

    :param name: metric name.
    :param sample: the sample to render.
    :return: the type token from the ``# TYPE`` line.
    """
    for line in exporter.render_prometheus_text([sample]).splitlines():
        if line.startswith(f"# TYPE {name} "):
            return line.split()[-1]
    return None


def _signalfx_envelope(sample):
    """Return the SignalFx envelope key a sample lands under.

    :param sample: the sample to wrap.
    :return: ``gauge`` or ``cumulative_counter``.
    """
    for envelope, points in exporter.to_signalfx_body([sample]).items():
        if points:
            return envelope
    return None


@pytest.mark.parametrize("name,expected", sorted(_EXPECTED.items()))
def test_metric_type_agrees_across_backends(name, expected):
    """GATE: Prometheus TYPE, SignalFx envelope, and OTLP Sum/Gauge all match the expected type."""
    sample = exporter._sample(name, 1.0, _DIMS, metric_type=_EXPLICIT.get(name))
    assert sample.metric_type == expected, f"{name} declared {sample.metric_type}, expected {expected}"

    assert _prometheus_type(name, sample) == expected

    assert _signalfx_envelope(sample) == (
        "cumulative_counter" if expected == "counter" else "gauge")

    pytest.importorskip("opentelemetry.sdk.metrics.export")
    from opentelemetry.sdk.metrics.export import Gauge, Sum

    from redfish_ctl.telemetry.otlp import metrics_data_from_samples
    data = (metrics_data_from_samples([sample], service_name="redfish_ctl")
            .resource_metrics[0].scope_metrics[0].metrics[0].data)
    if expected == "counter":
        assert isinstance(data, Sum) and data.is_monotonic
    else:
        assert isinstance(data, Gauge)


def test_is_monotonic_counter_covers_audit_cases():
    """The name classifier recognizes the counters the audit flagged (incl _total, _dropped)."""
    assert otlp.is_monotonic_counter("redfish_exporter_collection_errors_total")
    assert otlp.is_monotonic_counter("hw.fabric.vl15_dropped")
    assert otlp.is_monotonic_counter("hw.fabric.rx_bytes")
    assert not otlp.is_monotonic_counter("hw.scrape.duration_seconds")
    assert not otlp.is_monotonic_counter("hw.power")
