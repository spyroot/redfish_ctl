"""Offline tests for the single CLI error boundary: one ``Error:`` line, exit 1.

Two regressions guarded here, both surfaced by the env-gated fault-injection
decorators (:mod:`redfish_ctl.decorators.fault_injection`):

* BUG 1 -- a network/auth/cert failure printed ``Error: ...`` but the process
  exited 0, so scripts and automation read the failure as success.
* BUG 2 -- ``requests.exceptions.ReadTimeout`` (and the wider ``Timeout``
  family) was not caught at all, so a stalled BMC read dumped a raw traceback.

The design under test (G6-aligned): the command layer and ``_run`` catch
NOTHING. An operational error unwinds the stack -- the operation span records
it and marks ERROR as it propagates (OpenTelemetry ``start_as_current_span``
defaults), main's ``finally`` flushes finished spans (G6), and the single
boundary handler in ``redfish_main_ctl`` translates every ``CLI_HANDLED_ERRORS``
member into one clean ``Error:`` line and ``sys.exit(1)``. Command failures
take the same unwind path SIGTERM/SIGINT already use.

The probe-time tests drive the REAL ``IDracManager`` end-to-end through
``redfish_main_ctl`` with the SIMULATE_* flags set: the injected fault fires at
the ``api_get_call`` boundary before any socket is touched (just OUTSIDE the
CLIENT span a real transport fault would be recorded in -- the decorator wraps
the whole method), raising the same requests exception a genuine fault would.
No BMC, network, or credentials needed.

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
    InvalidArgumentFormat,
    InvalidJsonSpec,
    MissingMandatoryArguments,
    MissingResource,
    ResourceNotFound,
    TaskIdUnavailable,
    UncommittedPendingChanges,
    UnexpectedResponse,
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

#: One instance of every operational error the boundary must translate.
_OPERATIONAL_ERRORS = [
    RedfishException("redfish failed"),
    TaskIdUnavailable("no task id"),
    MissingResource("missing resource"),
    InvalidJsonSpec("bad json spec"),
    ResourceNotFound("not found"),
    InvalidArgument("bad argument"),
    InvalidArgumentFormat("malformed argument"),
    FailedDiscoverAction("discover failed"),
    UnsupportedAction("unsupported"),
    MissingMandatoryArguments("missing args"),
    FileNotFoundError("no such file"),
    UncommittedPendingChanges("pending changes"),
    AuthenticationFailed("auth failed"),
    UnexpectedResponse("bmc returned 503"),
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    requests.exceptions.SSLError("tls handshake failed"),
    requests.exceptions.TooManyRedirects("redirect loop"),
    requests.exceptions.ChunkedEncodingError("body cut short"),
    requests.exceptions.ContentDecodingError("mislabeled encoding"),
    requests.exceptions.InvalidURL("bad host value"),
    ssl.SSLCertVerificationError("certificate verify failed"),
]


@pytest.fixture(autouse=True)
def _plain_cli_env(monkeypatch):
    """Pin the CLI environment so assertions are exact and deterministic.

    ``color_printer`` wraps messages in escape codes when ``TERM`` is a known
    color terminal (breaking exact-line checks); a SIMULATE_* flag leaking in
    from the caller's environment would trip the non-injection tests; and
    REDFISH_*/IDRAC_* endpoint variables would fight the explicit CLI flags
    the end-to-end tests pass.
    """
    monkeypatch.delenv("TERM", raising=False)
    for flag in (fi.SIMULATE_NETWORK_FAILURE, fi.SIMULATE_NETWORK_TIMEOUT,
                 fi.SIMULATE_SLOW_IO):
        monkeypatch.delenv(flag, raising=False)
    for var in _ENDPOINT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


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


def _stub_cmd_tree(parser, debug=False):
    """Register one bare ``foo`` subcommand in place of the full registry.

    :param parser: the root argument parser redfish_main_ctl builds.
    :param debug: accepted for signature compatibility; not used.
    :return: the command map for the single ``foo`` command.
    """
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("foo")
    return {"foo": Command("t", "foo")}


def _cli(monkeypatch):
    """Point argv at ``redfish_ctl <creds> foo`` and stub the command registry.

    :param monkeypatch: the pytest monkeypatch fixture.
    :return: None. ``rm.redfish_main_ctl()`` is ready to call afterwards.
    """
    monkeypatch.setattr(rm, "create_cmd_tree", _stub_cmd_tree)
    monkeypatch.setattr(sys, "argv", [
        "redfish_ctl", "--host", "mock-idrac", "--username", "u",
        "--password", "p", "foo",
    ])


def _error_lines(capsys):
    """Return the non-empty stdout lines captured so far, asserting no traceback.

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
        """Succeed the probe so dispatch is the failure under test."""
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
# BUG 1 + BUG 2 regression: injected probe faults, REAL manager, full CLI path  #
# --------------------------------------------------------------------------- #
def test_probe_connection_error_exits_nonzero(monkeypatch, capsys):
    """BUG 1: an unreachable BMC (ConnectionError at the probe) exits 1, not 0."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    _cli(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        rm.redfish_main_ctl()
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "simulated network failure" in lines[0]


def test_probe_read_timeout_exits_nonzero(monkeypatch, capsys):
    """BUG 2: a stalled read (ReadTimeout at the probe) prints one clean Error
    line and exits 1 -- previously an uncaught raw traceback."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    _cli(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        rm.redfish_main_ctl()
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "simulated network timeout" in lines[0]


# --------------------------------------------------------------------------- #
# The G6 design: main() swallows nothing; the span records the unwind           #
# --------------------------------------------------------------------------- #
def test_main_propagates_operational_errors(monkeypatch):
    """main() catches nothing: a probe-time fault unwinds out of it unchanged,
    the same path the G6 SIGTERM/SIGINT handling relies on."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(requests.exceptions.ReadTimeout):
        rm.main(_args(), _CMD_MAP)


def test_propagating_fault_marks_operation_span_error(span_exporter, monkeypatch):
    """An exception propagating through the operation span leaves it ERROR with
    a recorded exception event -- no manual record call anywhere in the path."""
    from opentelemetry.trace import StatusCode

    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(requests.exceptions.ReadTimeout):
        rm.main(_args(), _CMD_MAP)

    roots = [s for s in span_exporter.get_finished_spans() if s.parent is None]
    assert len(roots) == 1, [s.name for s in span_exporter.get_finished_spans()]
    assert roots[0].name == "foo"
    assert roots[0].status.status_code == StatusCode.ERROR
    events = [e for e in roots[0].events if e.name == "exception"]
    assert len(events) == 1
    assert events[0].attributes["exception.type"] == (
        "requests.exceptions.ReadTimeout"
    )


def test_unknown_command_raises_invalid_argument():
    """An unknown subcommand raises InvalidArgument for the boundary handler
    (defensive branch; argparse rejects unknown subcommands before main)."""
    with pytest.raises(InvalidArgument, match="Unknown command."):
        rm.main(_args(), {})


# --------------------------------------------------------------------------- #
# The single boundary: every operational error -> one Error: line, exit 1       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc", _OPERATIONAL_ERRORS, ids=lambda e: type(e).__name__)
def test_boundary_translates_every_operational_error(monkeypatch, capsys, exc):
    """Each CLI_HANDLED_ERRORS member raised by dispatch reaches the boundary
    and becomes exactly one Error: line and exit code 1 -- no traceback."""
    _install_raising_manager(monkeypatch, exc)
    _cli(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        rm.redfish_main_ctl()
    assert excinfo.value.code == 1
    lines = _error_lines(capsys)
    assert len(lines) == 1
    assert lines[0] == f"Error: {exc}"


def test_every_operational_error_is_in_the_boundary_tuple():
    """The parametrized list above and CLI_HANDLED_ERRORS cannot drift apart:
    every raised instance must be caught by the tuple, and every tuple member
    must be exercised by an instance (ConnectTimeout covers its bases)."""
    for exc in _OPERATIONAL_ERRORS:
        assert isinstance(exc, rm.CLI_HANDLED_ERRORS), type(exc).__name__
    exercised = {type(e) for e in _OPERATIONAL_ERRORS}
    for handled in rm.CLI_HANDLED_ERRORS:
        assert any(issubclass(t, handled) for t in exercised), handled.__name__


def test_boundary_does_not_catch_unexpected_exceptions(monkeypatch):
    """A non-operational exception (a bug) keeps its traceback: the boundary
    must not silence what it does not understand."""
    _install_raising_manager(monkeypatch, RuntimeError("a bug"))
    _cli(monkeypatch)
    with pytest.raises(RuntimeError, match="a bug"):
        rm.redfish_main_ctl()


def test_successful_command_exits_zero(monkeypatch, capsys):
    """The success path is untouched: the CLI returns normally (exit 0)."""

    class _OkManager(_RaisingManager):
        def sync_invoke(self, _type, _name, **_kwargs):
            return CommandResult({}, None, None, None)

    monkeypatch.setattr(rm, "IDracManager", _OkManager)
    monkeypatch.setattr(rm, "process_respond", lambda *a, **k: {})
    monkeypatch.setattr(rm, "json_printer", lambda *a, **k: None)
    _cli(monkeypatch)
    rm.redfish_main_ctl()  # must not raise
    assert "Error:" not in capsys.readouterr().out


def test_command_result_error_exits_nonzero(monkeypatch):
    """The in-band error channel exits 1 too: a command that reports failure
    via CommandResult.error (the condition the span contract marks the
    operation span ERROR for) must not let the process report success."""

    class _ErrManager(_RaisingManager):
        def sync_invoke(self, _type, _name, **_kwargs):
            return CommandResult(
                {"fault": "rejected"}, None, None, ValueError("bmc rejected"))

    monkeypatch.setattr(rm, "IDracManager", _ErrManager)
    monkeypatch.setattr(rm, "json_printer", lambda *a, **k: None)
    _cli(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        rm.redfish_main_ctl()
    assert excinfo.value.code == 1
