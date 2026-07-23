"""Offline tests: every user-visible CLI ``Error:`` exits non-zero, without a traceback.

Two regressions guarded here, both surfaced by the env-gated fault-injection
decorators (:mod:`redfish_ctl.decorators.fault_injection`):

* BUG 1 -- a network/auth/cert failure printed ``Error: ...`` but the process
  exited 0, so scripts and automation read the failure as success. Every
  exception handler in ``_run`` and the top-level backstop in
  ``redfish_main_ctl`` must ``sys.exit(1)`` after printing.
* BUG 2 -- ``requests.exceptions.ReadTimeout`` (and the wider ``Timeout``
  family) was not caught at all, so a stalled BMC read dumped a raw Python
  traceback. A timeout must print one clean ``Error:`` line and exit 1.

The probe-time tests drive the real ``IDracManager`` through ``rm.main`` with the
SIMULATE_* flags set: the injected fault fires at ``api_get_call`` before any
socket is touched, exactly where a real network fault would surface -- no BMC,
network, or credentials needed.

Author Mus <spyroot@gmail.com>
"""
import argparse
import collections
import ssl
import sys

import pytest
import requests

from redfish_ctl import redfish_main as rm
from redfish_ctl.cmd_exceptions import (
    AuthenticationFailed,
    FailedDiscoverAction,
    InvalidArgument,
    InvalidJsonSpec,
    MissingMandatoryArguments,
    MissingResource,
    ResourceNotFound,
    TaskIdUnavailable,
    UncommittedPendingChanges,
    UnsupportedAction,
)
from redfish_ctl.decorators import fault_injection as fi
from redfish_ctl.redfish_exceptions import RedfishException
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import tracing

Command = collections.namedtuple("Command", "type name")
_CMD_MAP = {"foo": Command("t", "foo")}

_ENDPOINT_ENV_VARS = (
    "REDFISH_IP", "REDFISH_USERNAME", "REDFISH_PASSWORD", "REDFISH_PORT",
    "IDRAC_IP", "IDRAC_USERNAME", "IDRAC_PASSWORD", "IDRAC_PORT",
)


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch):
    """Force color-less output and clear fault flags so assertions are exact.

    ``color_printer`` wraps the message in escape codes when ``TERM`` is a known
    color terminal, which would break the ``startswith("Error:")`` checks; and a
    SIMULATE_* flag leaking in from the caller's environment would trip the
    non-injection tests.
    """
    monkeypatch.delenv("TERM", raising=False)
    for flag in (fi.SIMULATE_NETWORK_FAILURE, fi.SIMULATE_NETWORK_TIMEOUT,
                 fi.SIMULATE_SLOW_IO):
        monkeypatch.delenv(flag, raising=False)


@pytest.fixture
def span_exporter():
    """Install an in-memory tracer so a test can assert the operation span state."""
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
        redfish_host="mock-idrac", redfish_username="u", redfish_password="p",
        redfish_port=443, use_http=False, debug=False, verify_ssl=False,
        otlp_traces=False, subcommand="foo", verbose=False, nocolor=False,
    )


def _error_lines(capsys):
    """Return the non-empty stdout lines captured so far.

    :param capsys: the pytest capture fixture.
    :return: list of stripped, non-empty stdout lines.
    """
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    return [ln for ln in captured.out.splitlines() if ln.strip()]


class _RaisingManager:
    """Offline manager whose dispatch raises the exception under test."""

    redfish_vendor = "Generic"
    exc = None  # set per-test via _install_raising_manager

    def __init__(self, **_kwargs):
        pass

    def check_api_version(self):
        """Succeed the probe so the dispatch handler is the one exercised."""
        return "6.0"

    def sync_invoke(self, _type, _name, **_kwargs):
        """Raise the exception under test in place of a real dispatch."""
        raise self.exc


def _install_raising_manager(monkeypatch, exc):
    """Point rm.IDracManager at a manager whose sync_invoke raises ``exc``.

    :param monkeypatch: the pytest monkeypatch fixture.
    :param exc: the exception instance ``sync_invoke`` raises.
    """
    manager = type("_Mgr", (_RaisingManager,), {"exc": exc})
    monkeypatch.setattr(rm, "IDracManager", manager)


# --------------------------------------------------------------------------- #
# BUG 1 + BUG 2 regression: injected probe-time faults through the real manager #
# --------------------------------------------------------------------------- #
def test_probe_connection_error_exits_nonzero(monkeypatch, capsys):
    """BUG 1: an unreachable BMC (ConnectionError at the probe) exits 1, not 0."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    with pytest.raises(SystemExit) as excinfo:
        rm.main(_args(), _CMD_MAP)
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "simulated network failure" in lines[0]


def test_probe_read_timeout_exits_nonzero(monkeypatch, capsys):
    """BUG 2: a stalled read (ReadTimeout at the probe) prints one clean Error
    line and exits 1 -- previously an uncaught raw traceback."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(SystemExit) as excinfo:
        rm.main(_args(), _CMD_MAP)
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "simulated network timeout" in lines[0]


def test_probe_timeout_marks_operation_span_error(span_exporter, monkeypatch):
    """A probe-time fault is recorded on the operation root span (ERROR status),
    which requires the probe to run inside _run's handled region."""
    from opentelemetry.trace import StatusCode

    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(SystemExit):
        rm.main(_args(), _CMD_MAP)

    roots = [s for s in span_exporter.get_finished_spans() if s.parent is None]
    assert len(roots) == 1, [s.name for s in span_exporter.get_finished_spans()]
    assert roots[0].name == "foo"
    assert roots[0].status.status_code == StatusCode.ERROR
    assert roots[0].attributes["error.type"] == "ReadTimeout"


# --------------------------------------------------------------------------- #
# Exit-code consistency: every handler in _run exits 1 after printing Error:    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc", [
    RedfishException("redfish failed"),
    TaskIdUnavailable("no task id"),
    MissingResource("missing resource"),
    InvalidJsonSpec("bad json spec"),
    ResourceNotFound("not found"),
    InvalidArgument("bad argument"),
    FailedDiscoverAction("discover failed"),
    UnsupportedAction("unsupported"),
    MissingMandatoryArguments("missing args"),
    FileNotFoundError("no such file"),
    UncommittedPendingChanges("pending changes"),
    AuthenticationFailed("auth failed"),
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    ssl.SSLCertVerificationError("certificate verify failed"),
], ids=lambda e: type(e).__name__)
def test_run_handler_exits_nonzero(monkeypatch, capsys, exc):
    """Every exception _run handles prints one Error: line and exits 1."""
    _install_raising_manager(monkeypatch, exc)
    with pytest.raises(SystemExit) as excinfo:
        rm.main(_args(), _CMD_MAP)
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0] == f"Error: {exc}"


def test_successful_command_exits_zero(monkeypatch, capsys):
    """The success path is untouched: main returns normally (exit 0)."""

    class _OkManager(_RaisingManager):
        def sync_invoke(self, _type, _name, **_kwargs):
            return CommandResult({}, None, None, None)

    monkeypatch.setattr(rm, "IDracManager", _OkManager)
    monkeypatch.setattr(rm, "process_respond", lambda *a, **k: {})
    monkeypatch.setattr(rm, "json_printer", lambda *a, **k: None)
    rm.main(_args(), _CMD_MAP)  # must not raise SystemExit
    assert "Error:" not in capsys.readouterr().out


def test_unknown_command_exits_nonzero(capsys):
    """An unknown subcommand prints Error: and exits 1 (was a bare return)."""
    with pytest.raises(SystemExit) as excinfo:
        rm.main(_args(), {})
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert lines == ["Error: Unknown command."]


# --------------------------------------------------------------------------- #
# Top-level backstop in redfish_main_ctl: transport/auth faults exit 1          #
# --------------------------------------------------------------------------- #
def _boom(exc):
    """Return a callable that raises ``exc`` (stands in for main failing)."""

    def _raise(*_a, **_k):
        raise exc

    return _raise


def _stub_cmd_tree(parser, debug=False):
    """Register one bare ``foo`` subcommand in place of the full registry.

    :param parser: the root argument parser redfish_main_ctl builds.
    :param debug: accepted for signature compatibility; not used.
    :return: the command map for the single ``foo`` command.
    """
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("foo")
    return {"foo": Command("t", "foo")}


@pytest.mark.parametrize("exc", [
    AuthenticationFailed("auth failed"),
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    ssl.SSLCertVerificationError("certificate verify failed"),
], ids=lambda e: type(e).__name__)
def test_top_level_backstop_exits_nonzero(monkeypatch, capsys, exc):
    """The redfish_main_ctl backstop prints one Error: line and exits 1 for
    transport/auth faults raised outside _run's handled region."""
    for var in _ENDPOINT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(rm, "create_cmd_tree", _stub_cmd_tree)
    monkeypatch.setattr(rm, "main", _boom(exc))
    monkeypatch.setattr(sys, "argv", [
        "redfish_ctl", "--host", "h", "--username", "u", "--password", "p",
        "foo",
    ])
    with pytest.raises(SystemExit) as excinfo:
        rm.redfish_main_ctl()
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0] == f"Error: {exc}"
