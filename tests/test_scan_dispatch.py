"""Offline test for the credential-less scan dispatch used by redfish_main.

In scan mode (bmc-scan, or discovery --network) redfish_main.main() dispatches via
``redfish_api.invoke(...)`` with EMPTY idrac_ip/username/password — deliberately
bypassing ``sync_invoke``'s non-empty-credential gate, because a segment scan has
no target host and needs no credentials. The command-level tests route through
``sync_invoke`` with fixture creds and so never exercise this production path;
this test does, for both scan commands. All network I/O is mocked.
"""
import requests

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _fake_get(url, **_):
    last = int(url.split("//")[1].split(":")[0].split(".")[-1])
    if last == 1:
        return _Resp(200, {"RedfishVersion": "1.15.0", "Product": "X"})
    if last == 2:
        return _Resp(403)
    raise requests.exceptions.ConnectionError("no route")


def _invoke_scan(api_type, name, **scan_kwargs):
    """Mimic redfish_main scan-mode dispatch: empty creds, invoke() not sync_invoke."""
    api = CommandBase(idrac_ip="", idrac_username="", idrac_password="")
    orig, requests.get = requests.get, _fake_get
    try:
        return api.invoke(
            api_type, name,
            idrac_ip="", username="", password="", port=443,
            insecure=True, is_http=False, **scan_kwargs,
        )
    finally:
        requests.get = orig


def test_bmc_scan_credential_less_invoke():
    """bmc-scan dispatched with empty credentials (production path) returns BMCs."""
    res = _invoke_scan(ApiRequestType.BmcScan, "bmc-scan", subnet="192.168.9.0/29")
    assert {r["IP"]: r["Auth"] for r in res.data} == {
        "192.168.9.1": "open", "192.168.9.2": "required"}


def test_discovery_network_credential_less_invoke():
    """discovery --network dispatched with empty credentials returns BMCs, no crawl."""
    res = _invoke_scan(ApiRequestType.Discovery, "discovery",
                       scan_network="192.168.9.0/29")
    assert {r["IP"]: r["Auth"] for r in res.data} == {
        "192.168.9.1": "open", "192.168.9.2": "required"}
