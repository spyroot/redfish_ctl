"""Native OTLP output: the hw.* contract mapped onto the OTLP data model.

The pure OTEL_* config helpers run always;
the MetricsData construction needs the OpenTelemetry SDK and is importorskip-ed
so it runs where `redfish_ctl[otlp]`/`[dev]` is installed and skips otherwise.
"""
import sys
import types
import warnings

import pytest

from redfish_ctl.config import otlp_protocol
from redfish_ctl.telemetry.exporter import MetricSample
from redfish_ctl.telemetry.otlp import resolve_otlp_config
from redfish_ctl.telemetry.otlp.emit import _build_exporter


def test_resolve_config_endpoint_and_headers_are_passthrough(monkeypatch):
    """Endpoint/headers are the SDK's job: env is never read here, only explicit args pass through.

    Since the config-loader decoupling, ``resolve_otlp_config`` interprets only
    the transport protocol. Endpoint and headers are passthrough: the app must
    NOT read ``OTEL_EXPORTER_OTLP_[METRICS_]ENDPOINT``/``HEADERS`` so the OTLP SDK
    resolves them itself (it already applies metrics>generic precedence).
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", raising=False)
    # Nothing explicit, nothing set: endpoint/headers None, protocol falls to grpc.
    ep, proto, headers = resolve_otlp_config()
    assert ep is None and headers is None and proto == "grpc"

    # Endpoint/headers env set but NO explicit arg: still None (the app never
    # reads them; the SDK will).
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://metrics:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=env")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_HEADERS", "authorization=env-metrics")
    ep, _, headers = resolve_otlp_config()
    assert ep is None and headers is None

    # Explicit args pass straight through, winning over any env.
    ep, _, headers = resolve_otlp_config(
        endpoint="http://flag:4317", headers="authorization=flag")
    assert ep == "http://flag:4317"
    assert headers == "authorization=flag"


def test_resolve_config_protocol_precedence(monkeypatch):
    """Protocol is interpreted: explicit arg wins, else metrics>generic env, else grpc."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", raising=False)
    assert resolve_otlp_config()[1] == "grpc"                 # default

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    assert resolve_otlp_config()[1] == "http/protobuf"        # generic env

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
    assert resolve_otlp_config()[1] == "grpc"                 # metrics wins over generic

    # An explicit protocol arg wins over any env.
    assert resolve_otlp_config(protocol="http/json")[1] == "http/json"


def test_otlp_protocol_metrics_over_generic_no_conflict(monkeypatch):
    """config.otlp_protocol: metrics>generic precedence with NO conflict and NO DeprecationWarning.

    This is the deliberate divergence from ``env_first``: the two OTEL_* protocol
    names are not a canonical/legacy alias pair, so a metric-signal value that
    DIFFERS from the generic one is valid precedence, not a
    :class:`ConfigurationConflict`, and setting only one does not warn.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", raising=False)
    assert otlp_protocol() is None                            # neither set -> None

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "http/protobuf")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # Differing values do not raise ConfigurationConflict and do not warn.
        assert otlp_protocol() == "http/protobuf"


class _RecordingExporter:
    """Fake OTLPMetricExporter that records the kwargs it was constructed with."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        """Record ``kwargs`` for the assertion; the real exporter opens a socket.

        :param kwargs: constructor keyword arguments to capture.
        """
        type(self).last_kwargs = dict(kwargs)


def _install_fake_grpc_exporter(monkeypatch):
    """Register a fake grpc OTLPMetricExporter module so ``_build_exporter`` needs no SDK.

    Injecting the leaf module directly into ``sys.modules`` short-circuits the
    import machinery (a full dotted name already present is returned without
    re-importing parents), so this works even where the OTLP SDK is absent.

    :param monkeypatch: pytest fixture used to set and auto-restore the module.
    """
    mod_name = "opentelemetry.exporter.otlp.proto.grpc.metric_exporter"
    fake = types.ModuleType(mod_name)
    fake.OTLPMetricExporter = _RecordingExporter
    monkeypatch.setitem(sys.modules, mod_name, fake)


def test_build_exporter_omits_endpoint_and_headers_when_none(monkeypatch):
    """With no explicit endpoint/headers, the exporter gets NO such kwargs (SDK reads OTEL_*)."""
    _install_fake_grpc_exporter(monkeypatch)
    _RecordingExporter.last_kwargs = {"sentinel": True}
    _build_exporter(endpoint=None, protocol="grpc", headers=None)
    assert _RecordingExporter.last_kwargs == {}


def test_build_exporter_passes_explicit_endpoint_and_headers(monkeypatch):
    """An explicitly resolved endpoint/headers ARE forwarded to the exporter."""
    _install_fake_grpc_exporter(monkeypatch)
    _build_exporter(endpoint="http://flag:4317", protocol="grpc",
                    headers="authorization=flag")
    assert _RecordingExporter.last_kwargs == {
        "endpoint": "http://flag:4317", "headers": "authorization=flag"}


def _samples():
    dims = {"host.name": "gb300-poc1-slot1", "server.address": "10.0.0.41",
            "bmc.ip": "10.0.0.21", "node": "slot1", "vendor": "supermicro",
            "deployment.environment.name": "nv72-gb300",
            "service.namespace": "hardware",
            "service.instance.id": "cb0377f1-e3b9-4da9-9275-71825b2c6434",
            "service.version": "2.0.0",
            "service.criticality": "critical"}
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
                "deployment.environment.name", "service.namespace",
                "service.instance.id", "service.version", "service.criticality"):
        assert key in res

    metrics = {m.name: m for m in rm.scope_metrics[0].metrics}
    assert set(metrics) == {"hw.power", "hw.gpu.power", "hw.fabric.rx_bytes"}

    # hw.power is an instantaneous Gauge; identity dims are NOT on the datapoint.
    assert isinstance(metrics["hw.power"].data, Gauge)
    dp = metrics["hw.power"].data.data_points[0]
    assert "host.name" not in dp.attributes and "bmc.ip" not in dp.attributes
    assert "deployment.environment.name" not in dp.attributes
    assert "service.namespace" not in dp.attributes
    assert "service.instance.id" not in dp.attributes

    # Per-metric dims stay on the datapoint.
    gpu_dp = metrics["hw.gpu.power"].data.data_points[0]
    assert gpu_dp.attributes.get("gpu") == "GPU_0"

    # rx_bytes is a monotonic cumulative Sum.
    rx = metrics["hw.fabric.rx_bytes"].data
    assert isinstance(rx, Sum)
    assert rx.is_monotonic is True
    assert rx.data_points[0].attributes.get("port") == "NVLink_0"
