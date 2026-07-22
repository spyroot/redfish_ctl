"""Offline tests for the generic DMTF TaskService blocking poll.

These drive ``redfish_ctl.redfish_manager.RedfishManager.fetch_task`` and
``get_task_state`` entirely offline: the HTTP seam (``api_get_call``) is replaced
with crafted ``#Task`` responses, so no BMC, network, or credentials are needed.
They assert the vendor-neutral path (poll ``/TaskService/Tasks``, DMTF
``TaskState``) works independently of the Dell ``/Oem/Dell/Jobs`` job model.

Author Mus spyroot@gmail.com
"""
import json
from pathlib import Path

import pytest

from redfish_ctl.cmd_exceptions import AuthenticationFailed
from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_task_state import TaskState, TaskStatus
from redfish_ctl.telemetry import tracing
from tests.test_utils import create_json_resp

REPO = Path(__file__).resolve().parent.parent
DELL_TASK_FIXTURE = (
    REPO / "tests" / "idrac_fixtures"
    / "_redfish_v1_TaskService_Tasks_JID_000000000001.json"
)


def _mgr() -> RedfishManager:
    """Return a RedfishManager bound to a dummy endpoint; no I/O until api_get_call."""
    return RedfishManager(redfish_ip="127.0.0.1")


def _task(state: str, status: str = "OK", percent: int = 0) -> dict:
    """Build a minimal DMTF #Task body with the given state/status/percent."""
    return {
        "@odata.type": "#Task.v1_7_0.Task",
        "Id": "JID_1",
        "TaskState": state,
        "TaskStatus": status,
        "PercentComplete": percent,
    }


def _no_sleep(monkeypatch) -> None:
    """Make the poll loop's inter-poll sleep a no-op so tests do not wait."""
    monkeypatch.setattr("redfish_ctl.redfish_manager.time.sleep", lambda *_: None)


def test_get_task_state_parses_dmtf_task():
    """A #Task body yields the matching DMTF TaskState/TaskStatus enums."""
    state, status = _mgr().get_task_state(create_json_resp(_task("Completed", "OK", 100)))
    assert state is TaskState.Completed
    assert status is TaskStatus.OK


def test_get_task_state_cancelling_two_l():
    """DMTF spells the transient cancel state 'Cancelling' (two l's); the Dell
    model misspells it 'Canceling'. The generic parser accepts the spec spelling."""
    state, _ = _mgr().get_task_state(create_json_resp(_task("Cancelling")))
    assert state is TaskState.Cancelling


def test_get_task_state_missing_key_is_none():
    """A body without TaskState/TaskStatus maps to (None, None) and never raises."""
    state, status = _mgr().get_task_state(create_json_resp({"Id": "JID_1"}))
    assert state is None and status is None


def test_get_task_state_committed_dell_fixture():
    """The committed Dell #Task fixture parses via the generic DMTF path; Dell
    serves spec-standard TaskState/TaskStatus keys on /TaskService/Tasks."""
    data = json.loads(DELL_TASK_FIXTURE.read_text())
    state, status = _mgr().get_task_state(create_json_resp(data))
    assert state is TaskState.Completed
    assert status is TaskStatus.OK


def test_fetch_task_returns_terminal_state(monkeypatch):
    """fetch_task returns immediately when the first poll shows a terminal state."""
    mgr = _mgr()
    done = create_json_resp(_task("Completed", "OK", 100))
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: done)
    assert mgr.fetch_task("JID_1", sleep_time=0) is TaskState.Completed


def test_fetch_task_blocks_until_completed(monkeypatch):
    """fetch_task keeps polling through Running until a terminal state appears."""
    mgr = _mgr()
    responses = iter(
        [
            create_json_resp(_task("Running", "OK", 10), status_code=202),
            create_json_resp(_task("Running", "OK", 60), status_code=202),
            create_json_resp(_task("Completed", "OK", 100), status_code=200),
        ]
    )
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: next(responses))
    _no_sleep(monkeypatch)
    assert mgr.fetch_task("JID_1", sleep_time=0) is TaskState.Completed


def test_fetch_task_wait_for_state_early_exit(monkeypatch):
    """wait_for_state returns as soon as that state appears, before a terminal one."""
    mgr = _mgr()
    running = create_json_resp(_task("Running"), status_code=202)
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: running)
    _no_sleep(monkeypatch)
    result = mgr.fetch_task("JID_1", sleep_time=0, wait_for_state=TaskState.Running)
    assert result is TaskState.Running


def test_fetch_task_cancelled_410_returns_last_state(monkeypatch):
    """A 410 (task reaped after cancel) stops the poll and returns the last state."""
    mgr = _mgr()
    responses = iter(
        [
            create_json_resp(_task("Cancelling"), status_code=202),
            create_json_resp({}, status_code=410),
        ]
    )
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: next(responses))
    _no_sleep(monkeypatch)
    assert mgr.fetch_task("JID_1", sleep_time=0) is TaskState.Cancelling


def test_fetch_task_401_raises(monkeypatch):
    """A 401 from the task service raises AuthenticationFailed."""
    mgr = _mgr()
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: create_json_resp({}, status_code=401))
    with pytest.raises(AuthenticationFailed):
        mgr.fetch_task("JID_1", sleep_time=0)


def test_fetch_task_timeout_returns_last_state(monkeypatch):
    """When a task never reaches terminal, fetch_task honours the timeout and
    returns the last observed state instead of blocking forever."""
    mgr = _mgr()
    running = create_json_resp(_task("Running"), status_code=202)
    monkeypatch.setattr(mgr, "api_get_call", lambda *a, **k: running)
    _no_sleep(monkeypatch)
    assert mgr.fetch_task("JID_1", sleep_time=0, timeout=0.02) is TaskState.Running


@pytest.fixture
def span_exporter():
    """Install an in-memory tracer so a test can assert EMITTED span topology."""
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


class _FakeSession:
    """Session stub whose .get returns queued responses.

    Mocking the session (not ``api_get_call``) keeps ``api_get_call``'s own CLIENT
    span, so the poll span's children are exercised end to end.
    """

    def __init__(self, responses):
        self._it = iter(responses)

    def get(self, *_args, **_kwargs):
        """Return the next queued response for a GET."""
        return next(self._it)


def test_fetch_task_emits_poll_span_with_client_children(span_exporter, monkeypatch):
    """The DMTF poll loop emits ONE INTERNAL poll span; each BMC check nests as a
    CLIENT child (call-stack nesting), and the required poll.* attributes are set."""
    mgr = _mgr()
    responses = [
        create_json_resp(_task("Running"), status_code=202),
        create_json_resp(_task("Completed", "OK", 100), status_code=200),
    ]
    # ONE shared session so the response iterator advances across polls; a fresh
    # session per call would replay response #0 forever and never reach terminal.
    session = _FakeSession(responses)
    monkeypatch.setattr(mgr, "_http_session", lambda: session)
    _no_sleep(monkeypatch)

    assert mgr.fetch_task("JID_1", sleep_time=0) is TaskState.Completed

    spans = span_exporter.get_finished_spans()
    poll = [s for s in spans if s.name == "redfish.task.poll"]
    assert len(poll) == 1, [s.name for s in spans]
    poll_span = poll[0]

    for key in (
        "poll.count",
        "poll.interval_ms",
        "poll.elapsed_ms",
        "poll.terminal_state",
        "redfish.task.state",
    ):
        assert key in poll_span.attributes, f"missing required poll attr {key}"
    assert poll_span.attributes["poll.count"] == 2
    assert poll_span.attributes["poll.terminal_state"] is True
    assert poll_span.attributes["redfish.task.state"] == "Completed"

    # Each BMC check nests UNDER the poll span — the call stack IS the span tree.
    clients = [s for s in spans if s.name == "redfish.bmc.request"]
    assert len(clients) == 2
    for child in clients:
        assert child.parent is not None
        assert child.parent.span_id == poll_span.context.span_id
