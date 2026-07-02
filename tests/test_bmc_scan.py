"""Offline test for bmc-scan — detect Redfish BMCs on a network segment.

Probes each host in a CIDR with one unauthenticated ``GET /redfish/v1``. A 200
with RedfishVersion is an open ServiceRoot; a 401/403 is an auth-locked BMC (the
ServiceRoot exists but requires a login — still a real BMC); anything else (404,
connection refused, timeout) is not Redfish. Read-only: one GET per host, no
credentials, no mutation. All network I/O is mocked — no real hosts are touched.
"""
import requests

from idrac_ctl.discovery.cmd_bmc_scan import BmcScan
from idrac_ctl.idrac_shared import ApiRequestType


class _Resp:
    """Minimal stand-in for requests.Response used by the probe."""

    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def test_probe_open_serviceroot():
    """A 200 ServiceRoot with RedfishVersion is reported as an open BMC."""
    def fake_get(url, **_):
        return _Resp(200, {
            "RedfishVersion": "1.14.0", "Product": "PowerEdge",
            "Oem": {"Dell": {}},
            "Managers": {"@odata.id": "/redfish/v1/Managers"},
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
        })
    orig, requests.get = requests.get, fake_get
    try:
        row = BmcScan._probe("10.0.0.5", 443, 2)
    finally:
        requests.get = orig
    assert row["IP"] == "10.0.0.5"
    assert row["Auth"] == "open"
    assert row["RedfishVersion"] == "1.14.0"
    assert row["Vendor"] == ["Dell"]
    assert row["Systems"] == "/redfish/v1/Systems"


def test_probe_auth_locked_serviceroot():
    """A 403/401 ServiceRoot is still detected as a BMC, marked Auth=required."""
    for status in (401, 403):
        orig, requests.get = requests.get, lambda url, **_: _Resp(status)
        try:
            row = BmcScan._probe("10.0.0.6", 443, 2)
        finally:
            requests.get = orig
        assert row is not None, f"status {status} should still detect a BMC"
        assert row["Auth"] == "required"
        assert row["IP"] == "10.0.0.6"
        assert row["RedfishVersion"] is None


def test_probe_not_redfish_is_none():
    """A 404, a non-Redfish 200, or a connection error is not a BMC (None)."""
    # 404 — no ServiceRoot here
    orig, requests.get = requests.get, lambda url, **_: _Resp(404)
    try:
        assert BmcScan._probe("10.0.0.7", 443, 2) is None
    finally:
        requests.get = orig
    # 200 but no RedfishVersion — some other web server on 443
    orig, requests.get = requests.get, lambda url, **_: _Resp(200, {"hello": "world"})
    try:
        assert BmcScan._probe("10.0.0.8", 443, 2) is None
    finally:
        requests.get = orig

    # connection refused / timeout — raises, treated as no host
    def boom(url, **_):
        raise requests.exceptions.ConnectTimeout("timed out")
    orig, requests.get = requests.get, boom
    try:
        assert BmcScan._probe("10.0.0.9", 443, 2) is None
    finally:
        requests.get = orig


def test_execute_requires_subnet(redfish_mock_factory):
    """execute with no --subnet returns an error result, not a crash."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.BmcScan, "bmc-scan")
    assert res.data == []
    assert res.error and "subnet" in res.error


def test_execute_invalid_subnet(redfish_mock_factory):
    """execute with a malformed CIDR reports an invalid-subnet error."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.BmcScan, "bmc-scan", subnet="not-a-cidr")
    assert res.data == []
    assert res.error and "invalid subnet" in res.error


def test_execute_scan_finds_only_bmcs(redfish_mock_factory):
    """Scanning a small CIDR returns only the open + auth-locked BMCs, not dark hosts."""
    mgr, _ = redfish_mock_factory("supermicro")

    def fake_get(url, **_):
        last = int(url.split("//")[1].split(":")[0].split(".")[-1])
        if last == 1:      # open Redfish BMC
            return _Resp(200, {"RedfishVersion": "1.15.0", "Product": "X"})
        if last == 2:      # auth-locked BMC
            return _Resp(403)
        raise requests.exceptions.ConnectionError("no route")   # dark host

    orig, requests.get = requests.get, fake_get
    try:
        res = mgr.sync_invoke(ApiRequestType.BmcScan, "bmc-scan", subnet="192.168.9.0/29")
    finally:
        requests.get = orig

    found = {r["IP"]: r["Auth"] for r in res.data}
    assert found == {"192.168.9.1": "open", "192.168.9.2": "required"}
