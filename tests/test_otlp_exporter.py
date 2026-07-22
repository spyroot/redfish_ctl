"""Native OTLP output: the hw.* contract mapped onto the OTLP data model.

The pure helpers (counter classification, OTEL_* config resolution) run always;
the MetricsData construction needs the OpenTelemetry SDK and is importorskip-ed
so it runs where `redfish_ctl[otlp]`/`[dev]` is installed and skips otherwise.
"""
import pytest

from redfish_ctl.telemetry.exporter import MetricSample
from redfish_ctl.telemetry.otlp import (
    is_monotonic_counter,
    resolve_otlp_config,
)


def test_counter_classification():
    """Cumulative totals are Sums; instantaneous values stay Gauges."""
    assert is_monotonic_counter("hw.fabric.rx_bytes")
    assert is_monotonic_counter("hw.fabric.crc_errors")
    assert is_monotonic_counter("hw.fabric.link_down_count")
    assert is_monotonic_counter("hw.energy_kwh")
    assert not is_monotonic_counter("hw.power")
    assert not is_monotonic_counter("hw.temperature")
    assert not is_monotonic_counter("hw.fabric.rx_gbps")   # a rate, not a total
    assert not is_monotonic_counter("hw.fabric.link_up")   # a boolean state


def test_resolve_config_prefers_flags_then_metric_then_generic_env(monkeypatch):
    """Explicit args win; else metric-specific OTEL_* env; else generic; else grpc default."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    ep, proto, _ = resolve_otlp_config()
    assert ep is None and proto == "grpc"

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://metrics:4317")
    ep, _, _ = resolve_otlp_config()
    assert ep == "http://metrics:4317"          # metric-specific wins
    ep, _, _ = resolve_otlp_config(endpoint="http://flag:4317")
    assert ep == "http://flag:4317"             # explicit flag wins over env


def _samples():
    dims = {"host.name": "gb300-poc1-slot1", "server.address": "10.0.0.41",
            "bmc.ip": "10.0.0.21", "node": "slot1", "vendor": "supermicro",
            "deployment.environment.name": "nv72-gb300"}
    return [
        MetricSample("hw.power", 512.0, dict(dims), unit="W"),
        MetricSample("hw.gpu.power", 700.0, {**dims, "gpu": "GPU_0"}, unit="W"),
        MetricSample("hw.fabric.rx_bytes", 12345.0, {**dims, "port": "NVLink_0"}, unit="By",
                     metric_type="counter"),
    ]


def test_metrics_data_maps_contract():
    """Resource attrs, datapoint attrs, and Gauge-vs-Sum are mapped per the contract."""
    pytest.importorskip("opentelemetry.sdk.metrics.export")
    from opentelemetry.sdk.metrics.export import Gauge, Sum

    from redfish_ctl.telemetry.otlp import metrics_data_from_samples

    md = metrics_data_from_samples(_samples(), service_name="redfish_ctl")
    rm = md.resource_metrics[0]

    res = dict(rm.resource.attributes)
    assert res["service.name"] == "redfish_ctl"
    for key in ("host.name", "server.address", "bmc.ip", "node", "vendor",
                "deployment.environment.name"):
        assert key in res

    metrics = {m.name: m for m in rm.scope_metrics[0].metrics}
    assert set(metrics) == {"hw.power", "hw.gpu.power", "hw.fabric.rx_bytes"}

    # hw.power is an instantaneous Gauge; identity dims are NOT on the datapoint.
    assert isinstance(metrics["hw.power"].data, Gauge)
    dp = metrics["hw.power"].data.data_points[0]
    assert "host.name" not in dp.attributes and "bmc.ip" not in dp.attributes
    assert "deployment.environment.name" not in dp.attributes

    # Per-metric dims stay on the datapoint.
    gpu_dp = metrics["hw.gpu.power"].data.data_points[0]
    assert gpu_dp.attributes.get("gpu") == "GPU_0"

    # rx_bytes is a monotonic cumulative Sum.
    rx = metrics["hw.fabric.rx_bytes"].data
    assert isinstance(rx, Sum)
    assert rx.is_monotonic is True
    assert rx.data_points[0].attributes.get("port") == "NVLink_0"
