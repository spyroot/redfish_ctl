"""Offline tests for the optional OpenTelemetry tracing scaffold.

A command run through ``sync_invoke`` should, when tracing is enabled, emit an
operation root span named by the command plus a ``SpanKind.CLIENT`` span per BMC
HTTP call. The CLIENT span carries ``peer.service="bmc"`` so an APM backend
renders the BMC as one inferred downstream node. With tracing disabled (the
default) commands must behave exactly as before and emit nothing.

These use an in-memory span exporter — no collector, no network — and skip
cleanly when the OpenTelemetry SDK (the ``[otlp]`` extra) is not installed.

Author Mus <spyroot@gmail.com>
"""
import pytest

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import tracing


@pytest.fixture
def span_exporter():
    """Install an in-memory tracer for the duration of one test."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing.enable_tracing(provider.get_tracer("redfish_ctl.test"))
    try:
        yield exporter
    finally:
        tracing.disable_tracing()


def test_command_emits_operation_root_and_client_bmc_spans(span_exporter, redfish_mock):
    """One command yields its operation span plus CLIENT spans to the BMC.

    This is the whole feature on the smallest surface: the operation appears in
    an APM service map as ``redfish-ctl -> bmc`` (via peer.service) with a
    waterfall.
    """
    result = redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    assert isinstance(result, CommandResult)

    spans = span_exporter.get_finished_spans()
    names = [s.name for s in spans]

    # Operation root span, named by the command.
    assert "system_query" in names

    # At least one CLIENT span representing a BMC HTTP call.
    client_spans = [s for s in spans if s.name == "redfish.bmc.request"]
    assert client_spans, f"no CLIENT bmc spans among {names}"

    attrs = dict(client_spans[0].attributes)
    # peer.service is the make-or-break attribute: it collapses all BMCs into
    # one inferred downstream node instead of one node per address.
    assert attrs.get("peer.service") == "bmc"
    assert attrs.get("http.request.method") == "GET"
    assert "server.address" in attrs


def test_client_span_is_child_of_the_operation_span(span_exporter, redfish_mock):
    """CLIENT bmc spans nest under the operation span (the waterfall shape)."""
    redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    spans = span_exporter.get_finished_spans()

    by_id = {s.context.span_id: s for s in spans}
    op = next(s for s in spans if s.name == "system_query")
    client = next(s for s in spans if s.name == "redfish.bmc.request")

    # Walk the parent chain from the client span up to the operation span.
    node = client
    seen = set()
    while node is not None and node.parent is not None and node.context.span_id not in seen:
        seen.add(node.context.span_id)
        node = by_id.get(node.parent.span_id)
        if node is not None and node.context.span_id == op.context.span_id:
            break
    assert node is not None and node.context.span_id == op.context.span_id


def test_disabled_tracing_emits_nothing_and_still_works(redfish_mock):
    """The default (no tracer) path runs the command and records no spans."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Deliberately do NOT enable_tracing — the default no-op state.
    tracing.disable_tracing()

    result = redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    assert isinstance(result, CommandResult)
    assert exporter.get_finished_spans() == ()


def test_helpers_are_noop_without_a_tracer():
    """The span helpers must be safe to call with tracing off."""
    tracing.disable_tracing()
    assert tracing.is_enabled() is False
    with tracing.operation_span("x") as span:
        assert span is None
    with tracing.client_span("https://10.0.0.1/redfish/v1/", "GET") as span:
        assert span is None
    # record_* on a None span must not raise.
    tracing.record_response(None, 500)
    tracing.record_exception(None, RuntimeError("x"))
    tracing.record_result(None, CommandResult(None, None, None, "err"))
