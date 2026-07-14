"""Offline test for the bmc-scan command (segment BMC discovery).

Command-level behavior: subnet validation and dispatch through the manager. The
scan engine itself (probe, CIDR expansion, auth-locked detection) is covered in
test_net_scan.py. All network I/O is mocked — no real hosts are touched.
"""
import requests

from redfish_ctl.redfish_manager_shared import ApiRequestType


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


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
