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

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.system.cmd_system import SystemQuery

HOST_A = "10.9.9.1"
HOST_B = "10.9.9.2"


def _cmd(host, password="mock", is_http=False):
    return SystemQuery(
        idrac_ip=host, idrac_username="root",
        idrac_password=password, insecure=True, is_http=is_http,
    )


def test_constructor_accepts_canonical_connection_keywords():
    """Commands accept host/username/password/port without legacy keyword names."""
    cmd = SystemQuery(
        host="10.9.9.30",
        username="admin",
        password="secret",
        port=8443,
        insecure=True,
    )

    assert cmd.host == "10.9.9.30:8443"
    assert cmd.idrac_ip == "10.9.9.30:8443"
    assert cmd.username == "admin"
    assert cmd.password == "secret"


def test_canonical_connection_keywords_are_keyed_per_host():
    """Canonical connection keywords keep command singletons per BMC."""
    a = SystemQuery(
        host="10.9.9.31",
        username="admin",
        password="secret-a",
        port=443,
        insecure=True,
    )
    b = SystemQuery(
        host="10.9.9.32",
        username="admin",
        password="secret-b",
        port=443,
        insecure=True,
    )

    assert a is not b
    assert a.host == "10.9.9.31"
    assert b.host == "10.9.9.32"


def test_singleton_key_uses_canonical_alias_precedence():
    """When aliases conflict, singleton keying matches constructor precedence."""
    a = SystemQuery(
        host="10.9.9.33",
        idrac_ip="10.9.9.34",
        username="admin",
        idrac_username="legacy",
        password="secret",
        idrac_password="legacy-secret",
        port=443,
        idrac_port=8443,
        insecure=True,
    )
    b = SystemQuery(
        host="10.9.9.33",
        idrac_ip="10.9.9.35",
        username="admin",
        idrac_username="other-legacy",
        password="secret",
        idrac_password="other-legacy-secret",
        port=443,
        idrac_port=9443,
        insecure=True,
    )

    assert a is b
    assert a.host == "10.9.9.33"


def test_dispatch_connection_pop_cleans_mixed_public_aliases():
    """External invoke kwargs do not leak duplicate connection aliases to commands."""
    kwargs = {
        "host": "10.9.9.40",
        "idrac_ip": "10.9.9.41",
        "path": "/redfish/v1/",
    }

    value = RedfishManagerBase._pop_connection_value(
        kwargs, "host", "idrac_ip", "_redfish_host")

    assert value == "10.9.9.40"
    assert "host" not in kwargs
    assert "idrac_ip" not in kwargs
    assert kwargs == {"path": "/redfish/v1/"}


def test_dispatch_connection_pop_falls_back_when_canonical_is_none():
    """A None canonical value keeps the deprecated alias fallback working."""
    kwargs = {"host": None, "idrac_ip": "10.9.9.42"}

    value = RedfishManagerBase._pop_connection_value(
        kwargs, "host", "idrac_ip", "_redfish_host")

    assert value == "10.9.9.42"
    assert kwargs == {}


def test_internal_dispatch_connection_key_preserves_command_host_arg():
    """Internal connection keys avoid consuming subcommand-local host arguments."""
    kwargs = {
        "_redfish_host": "10.9.9.43",
        "idrac_ip": "10.9.9.44",
        "host": "downloads.example.test",
    }

    value = RedfishManagerBase._pop_connection_value(
        kwargs, "host", "idrac_ip", "_redfish_host")

    assert value == "10.9.9.43"
    assert kwargs == {"host": "downloads.example.test"}


def test_dispatch_constructs_registered_commands_with_legacy_keywords():
    """Registered commands with legacy-only constructors still dispatch safely."""

    class LegacyConstructorCommand(
            RedfishManagerBase,
            scm_type=ApiRequestType.SystemQuery,
            name="legacy-constructor-compat"):
        constructed = None

        def __init__(
                self, idrac_ip, idrac_username, idrac_password, idrac_port,
                insecure=True, is_http=False):
            """Record legacy constructor kwargs and initialize the base manager.

            :param idrac_ip: BMC host passed by dispatch.
            :param idrac_username: BMC username passed by dispatch.
            :param idrac_password: BMC password passed by dispatch.
            :param idrac_port: BMC port passed by dispatch.
            :param insecure: skip TLS verification flag.
            :param is_http: plain HTTP transport flag.
            :return: None.
            """
            self.__class__.constructed = {
                "idrac_ip": idrac_ip,
                "idrac_username": idrac_username,
                "idrac_password": idrac_password,
                "idrac_port": idrac_port,
                "insecure": insecure,
                "is_http": is_http,
            }
            super().__init__(
                idrac_ip=idrac_ip,
                idrac_username=idrac_username,
                idrac_password=idrac_password,
                idrac_port=idrac_port,
                insecure=insecure,
                is_http=is_http,
            )

        def execute(self, **kwargs):
            """Return execution kwargs so dispatch leakage is visible.

            :param kwargs: command arguments left after connection dispatch.
            :return: command result wrapping the remaining kwargs.
            """
            return CommandResult(data=kwargs)

        @staticmethod
        def register_subcommand(cls):
            """Unused test parser hook required by the command base class.

            :param cls: command class.
            :return: None because the test invokes the registry directly.
            """
            return None

    result = RedfishManagerBase.invoke(
        ApiRequestType.SystemQuery,
        "legacy-constructor-compat",
        host="10.9.9.45",
        username="admin",
        password="secret",
        port=8443,
        insecure=True,
        is_http=False,
        path="/redfish/v1/",
    )

    assert LegacyConstructorCommand.constructed == {
        "idrac_ip": "10.9.9.45",
        "idrac_username": "admin",
        "idrac_password": "secret",
        "idrac_port": 8443,
        "insecure": True,
        "is_http": False,
    }
    assert result.data == {"path": "/redfish/v1/"}


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
