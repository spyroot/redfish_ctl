"""Env-gated fault-injection decorators for live edge-case testing.

A Redfish client meets two failure edges that a healthy BMC and an offline mock both
hide: a signal (SIGTERM/SIGINT) arriving while a request is in flight, and a network
read that stalls or drops. Signal-vs-event-loop ordering and socket timing cannot be
reproduced faithfully with mocks, so these decorators sit at fixed points on the real
sync and async HTTP call methods. Each is a NO-OP unless its env flag is set, so
production and normal test runs pay only a single ``os.getenv`` check.

Flags (``1``/``true``/``yes``/``on`` enables; unset or any other value disables):

  ==========================  ================================================
  Flag                        Effect at each decorated HTTP call
  ==========================  ================================================
  SIMULATE_NETWORK_FAILURE    raise the caller's failure exception first
  SIMULATE_NETWORK_TIMEOUT    raise the caller's timeout exception first
  SIMULATE_SLOW_IO            sleep REDFISH_FAULT_DELAY_SECONDS (default 8s)
  ==========================  ================================================

The delay opens a deterministic window in which a signal can be delivered while a
request is nominally in flight, exercising the flush-on-termination path (G6). The
exception flags drive the ERROR span / non-zero-exit path a healthy BMC never
produces. The concrete exception type is supplied by the caller (``exception_factory``)
so the raised error matches what the real transport would raise (e.g. a ``requests``
exception), keeping downstream error handling and the recorded span identical to a
genuine fault.

This module stays transport-neutral on purpose: it never imports ``requests`` or any
client library, so it is reusable anywhere. Callers pass the exception factories.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import asyncio
import functools
import os
import time
from typing import Awaitable, Callable, TypeVar

try:  # ParamSpec is stdlib on the project's 3.10 floor; guard kept for older typing
    from typing import ParamSpec
except ImportError:  # pragma: no cover - only reachable on <3.10
    from typing_extensions import ParamSpec

#: Env flag: raise the caller-supplied failure exception at each decorated HTTP call.
SIMULATE_NETWORK_FAILURE = "SIMULATE_NETWORK_FAILURE"
#: Env flag: raise the caller-supplied timeout exception at each decorated HTTP call.
SIMULATE_NETWORK_TIMEOUT = "SIMULATE_NETWORK_TIMEOUT"
#: Env flag: sleep before each decorated HTTP call (opens a signal-injection window).
SIMULATE_SLOW_IO = "SIMULATE_SLOW_IO"
#: Env var overriding the SIMULATE_SLOW_IO delay length, in seconds.
DELAY_SECONDS_ENV = "REDFISH_FAULT_DELAY_SECONDS"

_DEFAULT_DELAY_SECONDS = 8.0
_TRUTHY = frozenset({"1", "true", "yes", "on"})

P = ParamSpec("P")
R = TypeVar("R")

__all__ = [
    "SIMULATE_NETWORK_FAILURE",
    "SIMULATE_NETWORK_TIMEOUT",
    "SIMULATE_SLOW_IO",
    "DELAY_SECONDS_ENV",
    "flag_enabled",
    "delay_seconds",
    "inject_exception",
    "inject_async_exception",
    "inject_delay",
    "inject_async_delay",
    "simulate_http_faults",
    "simulate_http_faults_async",
]


def flag_enabled(flag_name: str) -> bool:
    """Report whether an env flag is set to a truthy value.

    :param flag_name: environment variable name to inspect.
    :return: True when the variable equals 1/true/yes/on (case-insensitive), else False.
    """
    return os.getenv(flag_name, "").strip().lower() in _TRUTHY


def delay_seconds() -> float:
    """Resolve the SIMULATE_SLOW_IO delay length in seconds.

    :return: DELAY_SECONDS_ENV parsed as a float, or 8.0 when unset or non-numeric.
    """
    try:
        return float(os.getenv(DELAY_SECONDS_ENV, ""))
    except ValueError:
        return _DEFAULT_DELAY_SECONDS


def inject_exception(
    flag_name: str,
    exception_factory: Callable[[], BaseException],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Build a sync decorator that raises before the wrapped call when a flag is set.

    :param flag_name: env flag gating the injection; unset leaves the wrapper transparent.
    :param exception_factory: zero-argument callable returning the exception to raise.
    :return: a decorator preserving the wrapped callable's name and signature.
    """
    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        """Guard ``function`` with the env-gated exception.

        :param function: the callable to wrap.
        :return: the guarded callable.
        """
        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Raise the injected exception when the flag is set, else call through.

            :return: the wrapped callable's result when the flag is unset.
            """
            if flag_enabled(flag_name):
                raise exception_factory()
            return function(*args, **kwargs)

        return wrapper

    return decorator


def inject_async_exception(
    flag_name: str,
    exception_factory: Callable[[], BaseException],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Async counterpart of :func:`inject_exception`.

    :param flag_name: env flag gating the injection; unset leaves the wrapper transparent.
    :param exception_factory: zero-argument callable returning the exception to raise.
    :return: a decorator preserving the wrapped coroutine's name and signature.
    """
    def decorator(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """Guard the coroutine ``function`` with the env-gated exception.

        :param function: the coroutine to wrap.
        :return: the guarded coroutine.
        """
        @functools.wraps(function)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Raise the injected exception when the flag is set, else await through.

            :return: the awaited result when the flag is unset.
            """
            if flag_enabled(flag_name):
                raise exception_factory()
            return await function(*args, **kwargs)

        return wrapper

    return decorator


def inject_delay(flag_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Build a sync decorator that sleeps before the wrapped call when a flag is set.

    The sleep opens a deterministic window for delivering a signal (SIGTERM/SIGINT)
    while a request is nominally in flight, so the flush-on-termination path runs.

    :param flag_name: env flag gating the delay; unset leaves the wrapper transparent.
    :return: a decorator preserving the wrapped callable's name and signature.
    """
    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        """Guard ``function`` with the env-gated delay.

        :param function: the callable to wrap.
        :return: the guarded callable.
        """
        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Sleep when the flag is set, then call through.

            :return: the wrapped callable's result.
            """
            if flag_enabled(flag_name):
                time.sleep(delay_seconds())
            return function(*args, **kwargs)

        return wrapper

    return decorator


def inject_async_delay(flag_name: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Async counterpart of :func:`inject_delay` using ``asyncio.sleep``.

    Sleeping on the event loop (not a worker thread) lets a signal delivered to the
    main thread interrupt the loop -- the interaction a thread-pool sleep and an
    offline mock both hide.

    :param flag_name: env flag gating the delay; unset leaves the wrapper transparent.
    :return: a decorator preserving the wrapped coroutine's name and signature.
    """
    def decorator(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """Guard the coroutine ``function`` with the env-gated async delay.

        :param function: the coroutine to wrap.
        :return: the guarded coroutine.
        """
        @functools.wraps(function)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Await asyncio.sleep when the flag is set, then await through.

            :return: the awaited result.
            """
            if flag_enabled(flag_name):
                await asyncio.sleep(delay_seconds())
            return await function(*args, **kwargs)

        return wrapper

    return decorator


def simulate_http_faults(
    connection_error_factory: Callable[[], BaseException],
    timeout_error_factory: Callable[[], BaseException],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Compose the standard sync HTTP fault stack into one decorator.

    Order at call time: SIMULATE_SLOW_IO delay first (the signal window), then
    SIMULATE_NETWORK_TIMEOUT, then SIMULATE_NETWORK_FAILURE.

    :param connection_error_factory: returns the exception a dropped connection raises.
    :param timeout_error_factory: returns the exception a stalled read raises.
    :return: a single decorator applying all three env-gated faults in order.
    """
    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        """Stack the three sync fault decorators onto ``function``.

        :param function: the callable to wrap.
        :return: the fully wrapped callable.
        """
        function = inject_exception(SIMULATE_NETWORK_FAILURE, connection_error_factory)(function)
        function = inject_exception(SIMULATE_NETWORK_TIMEOUT, timeout_error_factory)(function)
        function = inject_delay(SIMULATE_SLOW_IO)(function)
        return function

    return decorator


def simulate_http_faults_async(
    connection_error_factory: Callable[[], BaseException],
    timeout_error_factory: Callable[[], BaseException],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Async counterpart of :func:`simulate_http_faults`.

    :param connection_error_factory: returns the exception a dropped connection raises.
    :param timeout_error_factory: returns the exception a stalled read raises.
    :return: a single decorator applying all three env-gated faults in order.
    """
    def decorator(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """Stack the three async fault decorators onto the coroutine ``function``.

        :param function: the coroutine to wrap.
        :return: the fully wrapped coroutine.
        """
        function = inject_async_exception(SIMULATE_NETWORK_FAILURE, connection_error_factory)(function)
        function = inject_async_exception(SIMULATE_NETWORK_TIMEOUT, timeout_error_factory)(function)
        function = inject_async_delay(SIMULATE_SLOW_IO)(function)
        return function

    return decorator
