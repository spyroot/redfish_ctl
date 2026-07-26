"""Regression tests for concrete reader and writer ownership boundaries."""

from __future__ import annotations

import uuid

import pytest

from redfish_ctl.component_integrity.cmd_component_integrity import (
    QueryComponentIntegrity,
)
from redfish_ctl.ports.cmd_nvlink_ports import NvLinkPorts
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry.abstract_exporter_reader import AbstractExporterReader
from redfish_ctl.telemetry.abstract_exporter_writer import AbstractExporterWriter
from redfish_ctl.telemetry.metric_model import MetricSample
from redfish_ctl.telemetry.otlp import OtlpWriter
from redfish_ctl.telemetry.prometheus import PrometheusWriter
from redfish_ctl.telemetry.signalfx import SignalFxWriter
from redfish_ctl.telemetry.supermicro.cmd_exporter import Exporter
from redfish_ctl.telemetry.supermicro.cmd_metric_reports import MetricReports
from redfish_ctl.telemetry.supermicro.metric_catalog import metric_definition
from redfish_ctl.telemetry.supermicro.super_microexporter import (
    SupermicroExporterReader,
)

_INSTANCE_ID = str(uuid.UUID("cb0377f1-e3b9-4da9-9275-71825b2c6434"))

_TOLERANT_COLLECTORS = (NvLinkPorts, MetricReports, QueryComponentIntegrity)


class _ReaderSource:
    """Minimal manager surface consumed by ``SupermicroExporterReader``."""

    redfish_ip = "192.0.2.29"

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def _default_service_instance_id(self, _cache, do_async=False):
        return _INSTANCE_ID

    def sync_invoke(self, api_type, name, **kwargs):
        self.calls.append((api_type, name, kwargs))
        if name == self.failure:
            raise TimeoutError(f"{name} timed out")
        payloads = {
            "environment-metrics": {"metrics": []},
            "thermal": {"temperature_readings": []},
            "leak-detectors": {"detectors": []},
        }
        return CommandResult(payloads.get(name, []), None, None, None)


def _sample(metric="hw.power"):
    definition = metric_definition(metric)
    return MetricSample(
        metric=metric,
        value=1.0,
        dimensions={"vendor": "supermicro"},
        metric_type=definition.kind,
        unit=definition.unit,
    )


def test_supermicro_reader_owns_collector_plan_and_shared_cache():
    """One reader scrape invokes every collector with one response cache."""
    source = _ReaderSource()
    reader = SupermicroExporterReader(source)

    samples = reader.read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
        do_async=True,
        do_expanded=True,
    )

    assert isinstance(reader, AbstractExporterReader)
    assert [name for _api_type, name, _kwargs in source.calls] == [
        "environment-metrics",
        "thermal",
        "sensors",
        "nvlink-ports",
        "metric-reports",
        "leak-detectors",
        "network-adapters",
        "component-integrity",
    ]
    cache_ids = {id(kwargs["redfish_cache"]) for _api, _name, kwargs in source.calls}
    assert len(cache_ids) == 1
    assert all(kwargs["preserve_errors"] is True for _api, _name, kwargs in source.calls)
    assert any(sample.metric == "redfish_exporter_scrape_success" for sample in samples)


def test_supermicro_reader_preserves_collector_failure_health():
    """A collector timeout remains a failed scrape rather than empty success."""
    reader = SupermicroExporterReader(_ReaderSource(failure="metric-reports"))

    samples = reader.read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
    )

    by_metric = {}
    for sample in samples:
        by_metric.setdefault(sample.metric, []).append(sample)
    assert by_metric["redfish_exporter_scrape_success"][0].value == 0
    errors = by_metric["redfish_exporter_collection_errors_total"]
    assert any(
        sample.dimensions["collector"] == "metric-reports"
        and sample.dimensions["error"] == "timeout"
        for sample in errors
    )


def test_reader_preserves_last_success_and_cumulative_error_state(monkeypatch):
    """Reader-owned lifecycle metrics survive later scrape failures."""
    source = _ReaderSource()
    reader = SupermicroExporterReader(source)
    monkeypatch.setattr(exporter.time, "time", lambda: 1234.0)

    first = reader.read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
    )
    first_last_success = next(
        sample for sample in first
        if sample.metric == "redfish_exporter_last_success_timestamp_seconds"
    )
    assert first_last_success.value == 1234.0

    source.failure = "metric-reports"
    second = reader.read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
    )
    third = reader.read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
    )

    second_last_success = next(
        sample for sample in second
        if sample.metric == "redfish_exporter_last_success_timestamp_seconds"
    )
    assert second_last_success.value == 1234.0
    third_total = next(
        sample for sample in third
        if sample.metric == "redfish_exporter_collection_errors_total"
        and sample.dimensions["collector"] == "metric-reports"
        and sample.dimensions["error"] == "timeout"
    )
    assert third_total.value == 2.0


def test_unsupported_collector_does_not_make_failed_scrape_partial():
    """Unsupported collectors are healthy omissions, not usable results."""
    status = exporter.collector_scrape_status((
        exporter.CollectorResult(
            "metric-reports", True, False, 0.1, (), "timeout"),
        exporter.CollectorResult(
            "nvlink-ports", False, True, 0.1, (), None),
    ))

    assert status == (False, False)


@pytest.mark.parametrize("command_cls", _TOLERANT_COLLECTORS)
def test_tolerant_collectors_can_preserve_transport_errors(
        monkeypatch, command_cls):
    """Exporter mode re-raises failures hidden by tolerant standalone reads."""
    command = command_cls(host="mock-supermicro", username="root", password="mock")

    def fail_query(*_args, **_kwargs):
        raise TimeoutError("collector transport failed")

    monkeypatch.setattr(command, "base_query", fail_query)

    assert command.execute().data == []
    with pytest.raises(TimeoutError, match="collector transport failed"):
        command.execute(preserve_errors=True)


def test_supermicro_reader_rejects_cross_vendor_label():
    """A concrete Supermicro reader cannot silently emit Dell-labelled metrics."""
    reader = SupermicroExporterReader(_ReaderSource())
    with pytest.raises(ValueError, match="another vendor label"):
        reader.read(vendor="dell", service_instance_id=_INSTANCE_ID)


def test_vendor_catalog_is_not_registered_as_shared_dmtf_state():
    """NV72 metrics resolve only through the concrete Supermicro catalog."""
    with pytest.raises(KeyError):
        exporter.metric_definition("hw.gb300.memory_page_retirement_count")
    with pytest.raises(KeyError):
        exporter.metric_definition("hw.fabric.rx_bytes")

    dynamic = metric_definition("hw.gb300.memory_page_retirement_count")
    assert dynamic.kind == "counter"
    assert dynamic.family == "gb300"
    assert metric_definition("hw.fabric.rx_bytes").kind == "counter"
    assert exporter.metric_definition("hw.scrape.ok").family == "scrape"


def test_concrete_writers_implement_the_shared_writer_contract():
    """Every selectable backend is an AbstractExporterWriter implementation."""
    assert issubclass(PrometheusWriter, AbstractExporterWriter)
    assert issubclass(SignalFxWriter, AbstractExporterWriter)
    assert issubclass(OtlpWriter, AbstractExporterWriter)


def test_exporter_owns_reader_bound_to_selected_manager():
    """The registered command owns a reader bound to its Supermicro manager."""
    command = Exporter(host="owned-reader", username="root", password="mock")

    assert isinstance(command._reader, SupermicroExporterReader)
    assert command._reader._source is command
    assert command._writer is None


def test_exporter_once_only_coordinates_owned_reader_and_writer(monkeypatch):
    """The command delegates one scrape and one write without collector logic."""
    command = Exporter(host="mock-supermicro", username="root", password="mock")
    calls = []

    class Reader:
        metric_definition = staticmethod(metric_definition)

        @staticmethod
        def read(**kwargs):
            calls.append(("read", kwargs))
            return [_sample()]

    class Writer:
        def write_once(self, samples):
            calls.append(("write_once", list(samples)))
            return CommandResult("written", None, {"sample_count": 1}, None)

        def run(self, scrape_samples):
            raise AssertionError("once mode called run")

    writer = Writer()
    command._reader = Reader()
    monkeypatch.setattr(command, "_create_writer", lambda **_kwargs: writer)

    result = command.execute(once=True, vendor="supermicro")

    assert result.data == "written"
    assert command._writer is writer
    assert [name for name, _value in calls] == ["read", "write_once"]


def test_exporter_run_delegates_reader_callable_to_owned_writer(monkeypatch):
    """Long-running mode gives the writer the reader's scrape callable."""
    command = Exporter(host="mock-supermicro", username="root", password="mock")
    calls = []

    class Reader:
        metric_definition = staticmethod(metric_definition)

        @staticmethod
        def read(**kwargs):
            calls.append(("read", kwargs))
            return [_sample()]

    class Writer:
        def write_once(self, samples):
            raise AssertionError("run mode called write_once")

        def run(self, scrape_samples):
            calls.append(("run", scrape_samples()))

    writer = Writer()
    command._reader = Reader()
    monkeypatch.setattr(command, "_create_writer", lambda **_kwargs: writer)

    result = command.execute(once=False, vendor="supermicro")

    assert result.data is None
    assert command._writer is writer
    assert [name for name, _value in calls] == ["read", "run"]


def test_reader_collector_types_are_the_expected_registered_contract():
    """The concrete plan retains the intended DMTF plus Supermicro request types."""
    source = _ReaderSource()
    SupermicroExporterReader(source).read(
        vendor="supermicro",
        service_instance_id=_INSTANCE_ID,
    )
    assert [api_type for api_type, _name, _kwargs in source.calls] == [
        ApiRequestType.EnvironmentMetrics,
        ApiRequestType.Thermal,
        ApiRequestType.Sensors,
        ApiRequestType.NvLinkPorts,
        ApiRequestType.SupermicroMetricReports,
        ApiRequestType.LeakDetectors,
        ApiRequestType.NetworkAdapters,
        ApiRequestType.ComponentIntegrity,
    ]
