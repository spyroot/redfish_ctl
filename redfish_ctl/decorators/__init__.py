"""Reusable decorators for redfish_ctl.

Hosts env-gated fault-injection decorators (:mod:`redfish_ctl.decorators.fault_injection`)
used to exercise signal-handling and network-error edges against a live BMC, where an
offline mock cannot reproduce the real signal/timing/socket interaction.

Author Mus spyroot@gmail.com
"""
from .fault_injection import (
    DELAY_SECONDS_ENV,
    SIMULATE_NETWORK_FAILURE,
    SIMULATE_NETWORK_TIMEOUT,
    SIMULATE_SLOW_IO,
    delay_seconds,
    flag_enabled,
    inject_async_delay,
    inject_async_exception,
    inject_delay,
    inject_exception,
    simulate_http_faults,
    simulate_http_faults_async,
)

__all__ = [
    "DELAY_SECONDS_ENV",
    "SIMULATE_NETWORK_FAILURE",
    "SIMULATE_NETWORK_TIMEOUT",
    "SIMULATE_SLOW_IO",
    "delay_seconds",
    "flag_enabled",
    "inject_async_delay",
    "inject_async_exception",
    "inject_delay",
    "inject_exception",
    "simulate_http_faults",
    "simulate_http_faults_async",
]
