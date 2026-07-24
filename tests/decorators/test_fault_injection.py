"""Offline tests for the env-gated fault-injection decorators.

The decorators (:mod:`redfish_ctl.decorators.fault_injection`) let a live run inject a
delay (to open a signal window for the SIGTERM/SIGINT flush path) or a simulated
network error at the fixed sync/async HTTP call sites. They must be exact NO-OPS when
their flag is unset, raise the caller's exception when set, sleep only when the slow-IO
flag is set, and preserve the wrapped callable's identity. The integration tests prove
the wiring: the manager's real ``api_get_call`` / ``api_async_get_call`` raise the
requests-level error a genuine network fault would raise.

No BMC, no network, no signals here -- those are exercised in the live smoke.

Author Mus <spyroot@gmail.com>
"""
import asyncio

import pytest
import requests

from redfish_ctl.decorators import fault_injection as fi


class _Boom(RuntimeError):
    """Sentinel exception raised by the fault factories under test."""


# --------------------------------------------------------------------------- #
# flag_enabled / delay_seconds                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " on "])
def test_flag_enabled_truthy(monkeypatch, value):
    """Any of 1/true/yes/on (case-insensitive, trimmed) reads as enabled."""
    monkeypatch.setenv("X_FLAG", value)
    assert fi.flag_enabled("X_FLAG") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_flag_enabled_falsey(monkeypatch, value):
    """Anything else -- including empty -- reads as disabled."""
    monkeypatch.setenv("X_FLAG", value)
    assert fi.flag_enabled("X_FLAG") is False


def test_flag_enabled_unset(monkeypatch):
    """An unset variable is disabled (the production default)."""
    monkeypatch.delenv("X_FLAG", raising=False)
    assert fi.flag_enabled("X_FLAG") is False


def test_delay_seconds_default_and_override(monkeypatch):
    """delay_seconds honors the override and falls back to 8.0 when unset/non-numeric."""
    monkeypatch.delenv(fi.DELAY_SECONDS_ENV, raising=False)
    assert fi.delay_seconds() == 8.0
    monkeypatch.setenv(fi.DELAY_SECONDS_ENV, "2.5")
    assert fi.delay_seconds() == 2.5
    monkeypatch.setenv(fi.DELAY_SECONDS_ENV, "not-a-number")
    assert fi.delay_seconds() == 8.0


# --------------------------------------------------------------------------- #
# inject_exception / inject_async_exception                                    #
# --------------------------------------------------------------------------- #
def test_inject_exception_off_is_transparent(monkeypatch):
    """Flag unset: the wrapped call runs and its identity is preserved."""
    monkeypatch.delenv("F", raising=False)

    @fi.inject_exception("F", lambda: _Boom("nope"))
    def real(x):
        return x * 2

    assert real(3) == 6
    assert real.__name__ == "real"


def test_inject_exception_on_raises(monkeypatch):
    """Flag set: the factory's exception is raised before the wrapped call."""
    monkeypatch.setenv("F", "1")
    ran = []

    @fi.inject_exception("F", lambda: _Boom("boom"))
    def real():
        ran.append(True)

    with pytest.raises(_Boom, match="boom"):
        real()
    assert ran == []


def test_inject_async_exception(monkeypatch):
    """Async variant: transparent when off, raises when on."""
    @fi.inject_async_exception("F", lambda: _Boom("boom"))
    async def real(x):
        return x + 1

    monkeypatch.delenv("F", raising=False)
    assert asyncio.run(real(1)) == 2
    assert real.__name__ == "real"

    monkeypatch.setenv("F", "1")
    with pytest.raises(_Boom):
        asyncio.run(real(1))


# --------------------------------------------------------------------------- #
# inject_delay / inject_async_delay                                            #
# --------------------------------------------------------------------------- #
def test_inject_delay_sleeps_only_when_set(monkeypatch):
    """Flag gates the sleep; the delay length comes from delay_seconds()."""
    calls = []
    monkeypatch.setattr(fi.time, "sleep", lambda s: calls.append(s))
    monkeypatch.setenv(fi.DELAY_SECONDS_ENV, "4")

    @fi.inject_delay("SLOW")
    def real():
        return "ok"

    monkeypatch.delenv("SLOW", raising=False)
    assert real() == "ok"
    assert calls == []

    monkeypatch.setenv("SLOW", "1")
    assert real() == "ok"
    assert calls == [4.0]


def test_inject_async_delay(monkeypatch):
    """Async delay awaits asyncio.sleep for the configured duration when set."""
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(fi.asyncio, "sleep", fake_sleep)
    monkeypatch.setenv(fi.DELAY_SECONDS_ENV, "3")

    @fi.inject_async_delay("SLOW")
    async def real():
        return "ok"

    monkeypatch.delenv("SLOW", raising=False)
    assert asyncio.run(real()) == "ok"
    assert calls == []

    monkeypatch.setenv("SLOW", "1")
    assert asyncio.run(real()) == "ok"
    assert calls == [3.0]


# --------------------------------------------------------------------------- #
# simulate_http_faults composite (order: delay -> timeout -> failure)          #
# --------------------------------------------------------------------------- #
def _clear_http_flags(monkeypatch):
    """Unset every HTTP fault flag so a composite starts from a clean state."""
    for flag in (fi.SIMULATE_SLOW_IO, fi.SIMULATE_NETWORK_TIMEOUT, fi.SIMULATE_NETWORK_FAILURE):
        monkeypatch.delenv(flag, raising=False)


def test_simulate_http_faults_composite_order(monkeypatch):
    """Composite runs delay first, then timeout, then failure; all off is a no-op."""
    events = []
    monkeypatch.setattr(fi.time, "sleep", lambda s: events.append(("sleep", s)))
    monkeypatch.setenv(fi.DELAY_SECONDS_ENV, "1")

    @fi.simulate_http_faults(lambda: _Boom("failure"), lambda: TimeoutError("timeout"))
    def real():
        events.append(("real", None))
        return "ok"

    assert real.__name__ == "real"

    # All flags off -> pass-through.
    _clear_http_flags(monkeypatch)
    assert real() == "ok"
    assert events == [("real", None)]

    # Slow-IO only -> sleep, then the real call.
    events.clear()
    monkeypatch.setenv(fi.SIMULATE_SLOW_IO, "1")
    assert real() == "ok"
    assert events == [("sleep", 1.0), ("real", None)]

    # Slow-IO + timeout -> sleep, then timeout raised before the real call.
    events.clear()
    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(TimeoutError):
        real()
    assert events == [("sleep", 1.0)]

    # Failure alone (no slow-IO) -> failure raised, nothing slept or run.
    events.clear()
    _clear_http_flags(monkeypatch)
    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    with pytest.raises(_Boom, match="failure"):
        real()
    assert events == []


def test_simulate_http_faults_async_composite(monkeypatch):
    """Async composite: no-op when off, raises the failure factory when set."""
    @fi.simulate_http_faults_async(lambda: _Boom("failure"), lambda: TimeoutError("timeout"))
    async def real():
        return "ok"

    _clear_http_flags(monkeypatch)
    assert asyncio.run(real()) == "ok"
    assert real.__name__ == "real"

    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    with pytest.raises(_Boom, match="failure"):
        asyncio.run(real())


# --------------------------------------------------------------------------- #
# Integration: the wiring on the manager's real HTTP methods                   #
# --------------------------------------------------------------------------- #
_MOCK_URL = "https://mock-idrac/redfish/v1"


def test_manager_get_raises_connection_error(redfish_mock, monkeypatch):
    """SIMULATE_NETWORK_FAILURE makes the sync GET raise a requests ConnectionError."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    with pytest.raises(requests.exceptions.ConnectionError):
        redfish_mock.api_get_call(_MOCK_URL, {})


def test_manager_get_raises_read_timeout(redfish_mock, monkeypatch):
    """SIMULATE_NETWORK_TIMEOUT makes the sync GET raise a requests ReadTimeout."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_TIMEOUT, "1")
    with pytest.raises(requests.exceptions.ReadTimeout):
        redfish_mock.api_get_call(_MOCK_URL, {})


def test_manager_async_get_raises_connection_error(redfish_mock, monkeypatch):
    """The async GET raises the same ConnectionError before scheduling the request."""
    monkeypatch.setenv(fi.SIMULATE_NETWORK_FAILURE, "1")
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(requests.exceptions.ConnectionError):
            loop.run_until_complete(redfish_mock.api_async_get_call(loop, _MOCK_URL, {}))
    finally:
        loop.close()
