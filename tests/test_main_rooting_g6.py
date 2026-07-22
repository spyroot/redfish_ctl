"""Offline tests for the operation root in main (B) and G6 flush-on-exit.

``main()`` opens exactly one operation root span named by the command; the
version handshake and the dispatched command's BMC calls nest under it, so there
are no orphan roots. The ``finally`` flushes finished spans on every exit path —
normal return, ``KeyboardInterrupt`` (SIGINT), and ``SystemExit`` (the SIGTERM
handler's raise) — as the call stack unwinds. Everything runs offline: the HTTP
seam is faked, so no BMC, network, or credentials are needed.

Author Mus <spyroot@gmail.com>
"""
import argparse
import collections

import pytest

from redfish_ctl import redfish_main as rm
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import tracing

Command = collections.namedtuple("Command", "type name")
_CMD_MAP = {"foo": Command("t", "foo")}


@pytest.fixture
def span_exporter():
    """Install an in-memory tracer so a test can assert emitted span topology."""
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


def _args():
    """Return a minimal CLI namespace for the single known command ``foo``."""
    return argparse.Namespace(
        redfish_host="h", redfish_username="u", redfish_password="p",
        redfish_port=443, use_http=False, debug=False, verify_ssl=False,
        otlp_traces=False, subcommand="foo", verbose=False, nocolor=False,
    )


class _EmptyQuery:
    """Redfish query stub that reports itself empty (skips the query path)."""

    def is_empty(self):
        """Return True so main treats the request as having no query params."""
        return True


class _FakeManager:
    """Offline manager whose preflight + dispatch each emit one BMC client span."""

    redfish_vendor = "Generic"

    def __init__(self, **_kwargs):
        pass

    def check_api_version(self):
        """Emit one CLIENT span (the version handshake) and return a version."""
        with tracing.client_span("https://bmc/redfish/v1", "GET"):
            pass
        return "6.0"

    def sync_invoke(self, _type, _name, **_kwargs):
        """Emit one CLIENT span (the dispatched call) and return an empty result."""
        with tracing.client_span("https://bmc/redfish/v1/Systems", "GET"):
            pass
        return CommandResult({}, None, None, None)


def _boom(exc):
    """Return a callable that raises ``exc`` (to stand in for _run failing)."""

    def _raise(*_a, **_k):
        raise exc

    return _raise


def test_main_roots_preflight_and_dispatch_under_one_operation(span_exporter, monkeypatch):
    """main() opens ONE operation root named by the command; the preflight and the
    dispatched command's BMC calls nest under it — orphan roots == 0."""
    monkeypatch.setattr(rm, "IDracManager", _FakeManager)
    monkeypatch.setattr(rm, "_redfish_query_from_args", lambda *a, **k: _EmptyQuery())
    monkeypatch.setattr(rm, "process_respond", lambda *a, **k: {})
    monkeypatch.setattr(rm, "json_printer", lambda *a, **k: None)

    rm.main(_args(), _CMD_MAP)

    spans = span_exporter.get_finished_spans()
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1, [s.name for s in spans]
    assert roots[0].name == "foo"

    clients = [s for s in spans if s.name == "redfish.bmc.request"]
    assert len(clients) >= 2, "preflight + dispatch BMC spans expected"
    root_id = roots[0].context.span_id
    for child in clients:
        assert child.parent is not None, "orphan BMC span"
        assert child.parent.span_id == root_id, "BMC span not under the operation root"


def test_main_flushes_on_every_exit(monkeypatch):
    """main()'s finally flushes on normal return, KeyboardInterrupt (SIGINT), and
    SystemExit (the SIGTERM handler's raise); the exceptions still propagate."""
    flushes = []
    monkeypatch.setattr(rm.tracing, "shutdown", lambda *a, **k: flushes.append(1))

    monkeypatch.setattr(rm, "_run", lambda *a, **k: None)
    rm.main(_args(), _CMD_MAP)
    assert len(flushes) == 1

    monkeypatch.setattr(rm, "_run", _boom(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rm.main(_args(), _CMD_MAP)
    assert len(flushes) == 2

    monkeypatch.setattr(rm, "_run", _boom(SystemExit(143)))
    with pytest.raises(SystemExit):
        rm.main(_args(), _CMD_MAP)
    assert len(flushes) == 3
