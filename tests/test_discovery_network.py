"""Offline test for `discovery --network` — segment scan mode.

With --network (scan_network) the discovery command becomes a credential-less
BMC sweep of a segment (shared engine with bmc-scan): it returns a list of
detected BMCs and must NOT deep-crawl the single host or write the .npy map.
Without it, the original single-host crawl path is unchanged. Network I/O is
mocked; no real hosts are touched.
"""
import requests

from redfish_ctl.command_shared import ApiRequestType


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def test_discovery_network_scan_returns_bmcs(redfish_mock_factory):
    """discovery --network scans the segment and returns detected BMC rows."""
    mgr, _ = redfish_mock_factory("supermicro")

    def fake_get(url, **_):
        last = int(url.split("//")[1].split(":")[0].split(".")[-1])
        if last == 1:
            return _Resp(200, {"RedfishVersion": "1.15.0", "Product": "GB300",
                               "Vendor": "Supermicro"})
        if last == 2:
            return _Resp(403)
        raise requests.exceptions.ConnectionError("no route")

    orig, requests.get = requests.get, fake_get
    try:
        res = mgr.sync_invoke(ApiRequestType.Discovery, "discovery",
                              scan_network="192.168.9.0/29")
    finally:
        requests.get = orig

    found = {r["IP"]: r["Auth"] for r in res.data}
    assert found == {"192.168.9.1": "open", "192.168.9.2": "required"}
    assert res.data[0]["Vendor"] == "supermicro"


def test_discovery_network_invalid_cidr_errors(redfish_mock_factory):
    """A malformed --network CIDR returns an error result, not a crash/crawl."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.Discovery, "discovery",
                          scan_network="10.0.0.0/33")
    assert res.data == []
    assert res.error and "invalid network" in res.error
