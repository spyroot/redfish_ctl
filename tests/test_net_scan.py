"""Offline tests for the shared network-scan engine (discovery/net_scan.py).

The engine backs both ``bmc-scan`` and ``discovery --network``. It expands a
CIDR and issues one unauthenticated ``GET /redfish/v1`` per host: a 200 with
RedfishVersion is an open BMC (vendor classified from the ServiceRoot); a
401/403 is an auth-locked BMC (still reported, Auth=required); anything else is
not a BMC. All network I/O is mocked — no real hosts are touched.
"""
import requests

from idrac_ctl.discovery.net_scan import expand_cidr, probe_host, scan_segment


class _Resp:
    """Minimal stand-in for requests.Response used by the probe."""

    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _patch_get(fake):
    """Swap requests.get and return the original so callers can restore it."""
    orig, requests.get = requests.get, fake
    return orig


# --------------------------------------------------------------------------- #
# expand_cidr
# --------------------------------------------------------------------------- #

def test_expand_cidr_slash24_drops_network_and_broadcast():
    """A /24 expands to its 254 usable host addresses."""
    hosts = expand_cidr("192.168.9.0/24")
    assert len(hosts) == 254
    assert "192.168.9.0" not in hosts and "192.168.9.255" not in hosts
    assert hosts[0] == "192.168.9.1"


def test_expand_cidr_single_host_slash32():
    """A /32 has no host range, so the address itself is probed."""
    assert expand_cidr("10.43.3.209/32") == ["10.43.3.209"]


def test_expand_cidr_bare_address():
    """A bare address (no prefix) is treated as a single /32."""
    assert expand_cidr("10.0.0.5") == ["10.0.0.5"]


def test_expand_cidr_invalid_raises():
    """A malformed CIDR raises ValueError (surfaced as a command error upstream)."""
    import pytest
    with pytest.raises(ValueError):
        expand_cidr("not-a-cidr")


# --------------------------------------------------------------------------- #
# probe_host
# --------------------------------------------------------------------------- #

def test_probe_open_serviceroot_classifies_vendor():
    """A 200 ServiceRoot with RedfishVersion is an open BMC; vendor is classified."""
    orig = _patch_get(lambda url, **_: _Resp(200, {
        "RedfishVersion": "1.14.0", "Product": "PowerEdge R760",
        "Oem": {"Dell": {}},
        "Managers": {"@odata.id": "/redfish/v1/Managers"},
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
    }))
    try:
        row = probe_host("10.0.0.5", 443, 2)
    finally:
        requests.get = orig
    assert row["IP"] == "10.0.0.5"
    assert row["Auth"] == "open"
    assert row["Vendor"] == "dell"          # from the reused classify_vendor
    assert row["Product"] == "PowerEdge R760"
    assert row["Systems"] == "/redfish/v1/Systems"


def test_probe_auth_locked_serviceroot_still_detected():
    """A 401/403 ServiceRoot is still a BMC; reported Auth=required, vendor unknown."""
    for status in (401, 403):
        orig = _patch_get(lambda url, **_: _Resp(status))
        try:
            row = probe_host("10.0.0.6", 443, 2)
        finally:
            requests.get = orig
        assert row is not None, f"status {status} should still detect a BMC"
        assert row["Auth"] == "required"
        assert row["Vendor"] == "unknown"
        assert row["RedfishVersion"] is None


def test_probe_not_redfish_is_none():
    """A 404, a non-Redfish 200, or a connection error is not a BMC (None)."""
    orig = _patch_get(lambda url, **_: _Resp(404))
    try:
        assert probe_host("10.0.0.7", 443, 2) is None
    finally:
        requests.get = orig

    orig = _patch_get(lambda url, **_: _Resp(200, {"hello": "world"}))
    try:
        assert probe_host("10.0.0.8", 443, 2) is None
    finally:
        requests.get = orig

    def boom(url, **_):
        raise requests.exceptions.ConnectTimeout("timed out")
    orig = _patch_get(boom)
    try:
        assert probe_host("10.0.0.9", 443, 2) is None
    finally:
        requests.get = orig


# --------------------------------------------------------------------------- #
# scan_segment
# --------------------------------------------------------------------------- #

def test_scan_segment_finds_only_bmcs():
    """Scanning a small CIDR returns only the open + auth-locked BMCs."""
    def fake_get(url, **_):
        last = int(url.split("//")[1].split(":")[0].split(".")[-1])
        if last == 1:
            return _Resp(200, {"RedfishVersion": "1.15.0", "Product": "X"})
        if last == 2:
            return _Resp(403)
        raise requests.exceptions.ConnectionError("no route")

    orig = _patch_get(fake_get)
    try:
        rows = scan_segment("192.168.9.0/29", 443, 2, 8)
    finally:
        requests.get = orig
    found = {r["IP"]: r["Auth"] for r in rows}
    assert found == {"192.168.9.1": "open", "192.168.9.2": "required"}


def test_scan_segment_invalid_subnet_raises():
    """scan_segment propagates ValueError for a bad CIDR (caller renders it)."""
    import pytest
    with pytest.raises(ValueError):
        scan_segment("garbage/33", 443, 2, 8)
