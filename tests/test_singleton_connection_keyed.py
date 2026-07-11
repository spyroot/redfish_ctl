"""Regression tests: command instances must be isolated per BMC connection.

Command classes use ``metaclass=Singleton``. The original metaclass cached
one instance per class and ignored constructor arguments on every later
construction, so in any multi-BMC path (``fleet`` fan-out, the proxy, a
controller with several endpoints) every node silently reused the FIRST
node's IP, credentials, transport, and cached discovery state.

The contract pinned here: same (class, connection) reuses one instance —
BMC round-trips are expensive (hundreds of ms), so per-BMC caching must
survive — while a different connection gets its own isolated instance.

Author Mus spyroot@gmail.com
"""
import re
from concurrent.futures import ThreadPoolExecutor

import requests_mock as requests_mock_lib

from redfish_ctl.system.cmd_system import SystemQuery

HOST_A = "10.9.9.1"
HOST_B = "10.9.9.2"


def _cmd(host, password="mock", is_http=False):
    return SystemQuery(
        idrac_ip=host, idrac_username="root",
        idrac_password=password, insecure=True, is_http=is_http,
    )


def test_two_connections_get_distinct_instances():
    """Different BMCs must never share a command instance.

    This was the multi-BMC bug: the second construction returned the first
    cached object, still bound to the first BMC's address and transport.
    """
    a = _cmd(HOST_A, password="pw-a", is_http=True)
    b = _cmd(HOST_B, password="pw-b", is_http=False)

    assert a is not b
    assert a.idrac_ip == HOST_A
    assert b.idrac_ip == HOST_B
    assert a._default_method == "http://"
    assert b._default_method == "https://"


def test_same_connection_reuses_one_instance():
    """The same BMC reuses one instance, keeping its discovery caches warm."""
    a1 = _cmd(HOST_A)
    a2 = _cmd(HOST_A)

    assert a1 is a2


def test_cached_state_is_per_connection():
    """Connection-derived caches must not leak between BMCs.

    ``redfish_vendor`` is a cached_property backed by a live GET; it steers
    vendor-specific behavior, telemetry dimensions, and the proxy's vendor
    label, so one BMC's cached answer served for another mislabels and
    misroutes everything downstream.
    """
    a = _cmd(HOST_A)
    b = _cmd(HOST_B)

    with requests_mock_lib.Mocker() as m:
        m.get(re.compile(rf"https://{HOST_A}/.*"), json={"Vendor": "VendorA"})
        m.get(re.compile(rf"https://{HOST_B}/.*"), json={"Vendor": "VendorB"})

        assert a.redfish_vendor == "VendorA"
        assert b.redfish_vendor == "VendorB"


def test_http_requests_carry_their_own_connection():
    """Each instance's HTTP layer must target its own BMC address."""
    a = _cmd(HOST_A)
    b = _cmd(HOST_B)

    with requests_mock_lib.Mocker() as m:
        m.get(re.compile(rf"https://{HOST_A}/.*"), json={"Name": "node-a"})
        m.get(re.compile(rf"https://{HOST_B}/.*"), json={"Name": "node-b"})

        result_a = a.base_query("/redfish/v1/")
        result_b = b.base_query("/redfish/v1/")

    assert result_a.data["Name"] == "node-a"
    assert result_b.data["Name"] == "node-b"


def test_concurrent_first_construction_yields_one_instance():
    """Racing constructions of the same connection converge on one object.

    The fleet fan-out constructs commands from a thread pool; first-build
    must be locked or two threads can each create and use a half-shared
    instance.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: _cmd(HOST_A), range(16)))

    assert all(inst is instances[0] for inst in instances)
