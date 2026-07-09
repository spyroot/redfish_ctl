"""Offline tests for the read-only Redfish discovery package.

Covers the pure vendor classifier (ranking and fallbacks), the async scan helper
driven by a fake GET (no network), and the table renderer's plain-text/empty
paths. Everything here runs without an iDRAC, a network, or ``rich`` installed.

Author Mus spyroot@gmail.com
"""
import asyncio
import io

import pytest

from idrac_ctl.discover import (
    DiscoveredService,
    classify_vendor,
    scan_subnet,
)
from idrac_ctl.discover import cli as discover_cli

# --------------------------------------------------------------------------- #
# Vendor classifier
# --------------------------------------------------------------------------- #

def test_classify_dell_via_oem():
    """Dell ServiceRoot with an Oem.Dell block classifies as ``dell``."""
    service_root = {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "RedfishVersion": "1.11.0",
        "Oem": {"Dell": {"@odata.type": "#DellServiceRoot.v1_0_0.DellServiceRoot"}},
    }
    assert classify_vendor(service_root) == "dell"


def test_classify_hpe_via_oem():
    """HPE ServiceRoot with an Oem.Hpe block classifies as ``hpe``."""
    service_root = {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "Oem": {"Hpe": {"Manager": {"Type": "iLO 5"}}},
    }
    assert classify_vendor(service_root) == "hpe"


def test_classify_supermicro_via_vendor_field_no_oem():
    """Supermicro is recognized from Vendor text when no Oem block is present."""
    service_root = {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "Vendor": "Supermicro",
        "Product": "X11 BMC",
    }
    assert "Oem" not in service_root
    assert classify_vendor(service_root) == "supermicro"


def test_classify_openbmc_via_oem():
    """A ServiceRoot with an Oem.OpenBmc block classifies as ``openbmc``."""
    service_root = {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "Oem": {"OpenBmc": {"@odata.type": "#OemServiceRoot.v1_0_0.OemServiceRoot"}},
    }
    assert classify_vendor(service_root) == "openbmc"


def test_classify_openbmc_via_vendor_text():
    """OpenBMC is recognized from Vendor/Manufacturer text when no Oem block is present."""
    assert classify_vendor({"Manufacturer": "OpenBMC Project", "Product": "BMC"}) == "openbmc"


def test_classify_unknown_is_generic():
    """A ServiceRoot with no vendor signal falls back to ``generic``."""
    service_root = {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "RedfishVersion": "1.6.0",
    }
    assert classify_vendor(service_root) == "generic"


def test_classify_odata_type_prefix_ranks_above_text():
    """An OEM-prefixed @odata.type wins when there is no Oem block.

    Even with a misleading Manufacturer string, the @odata.type signal (ranked
    above free text) decides the vendor.
    """
    service_root = {
        "@odata.type": "#DellServiceRoot.v1_0_0.DellServiceRoot",
        "Manufacturer": "Supermicro",
    }
    assert classify_vendor(service_root) == "dell"


def test_classify_oem_ranks_above_odata_type():
    """The Oem child key outranks a conflicting @odata.type prefix."""
    service_root = {
        "@odata.type": "#HpeServiceRoot.v1_0_0.HpeServiceRoot",
        "Oem": {"Dell": {}},
    }
    assert classify_vendor(service_root) == "dell"


def test_classify_manufacturer_substring_case_insensitive():
    """Manufacturer matching is case-insensitive and substring-based."""
    service_root = {"Manufacturer": "DELL Inc."}
    assert classify_vendor(service_root) == "dell"


@pytest.mark.parametrize("bad", [None, [], "ServiceRoot", 42])
def test_classify_non_mapping_is_generic(bad):
    """Non-mapping / missing input never raises; it maps to ``generic``."""
    assert classify_vendor(bad) == "generic"


def test_classify_malformed_oem_falls_through_to_text():
    """A non-mapping Oem is ignored and later signals still apply."""
    service_root = {"Oem": ["Dell"], "Vendor": "Hewlett Packard Enterprise"}
    assert classify_vendor(service_root) == "hpe"


# --------------------------------------------------------------------------- #
# Async scan helper (fake GET, no network)
# --------------------------------------------------------------------------- #

def _make_fake_get(table):
    """Build a fake async GET that returns ``table[ip]`` or raises on KeyError.

    ``table`` maps host -> ServiceRoot dict (or ``None`` for "answered but not
    Redfish"). A host missing from the table raises, exercising the scanner's
    per-host error isolation.
    """
    async def fake_get(ip):
        if ip not in table:
            raise ConnectionError(f"unreachable: {ip}")
        return table[ip]

    return fake_get


def test_scan_subnet_returns_reachable_services():
    """Reachable hosts are returned with parsed vendor/product/version."""
    table = {
        "10.0.0.1": {
            "Oem": {"Dell": {}},
            "Product": "Integrated Dell Remote Access Controller",
            "RedfishVersion": "1.11.0",
        },
        "10.0.0.2": None,  # answered but not a Redfish service
        "10.0.0.3": {
            "Vendor": "Supermicro",
            "Product": "BMC",
            "RedfishVersion": "1.8.0",
        },
    }
    # 10.0.0.4 is absent from the table -> the fake GET raises for it.
    hosts = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]

    results = asyncio.run(scan_subnet(hosts, _make_fake_get(table), concurrency=2))

    assert [svc.ip for svc in results] == ["10.0.0.1", "10.0.0.3"]
    dell, smc = results
    assert dell.vendor == "dell"
    assert dell.redfish_version == "1.11.0"
    assert smc.vendor == "supermicro"
    assert smc.product == "BMC"


def test_scan_subnet_preserves_input_order_and_dedupes():
    """Results follow first-seen input order; duplicate/empty hosts are dropped."""
    table = {
        "a": {"Vendor": "Dell"},
        "b": {"Vendor": "Supermicro"},
    }
    hosts = ["b", "a", "b", "", "a"]
    results = asyncio.run(scan_subnet(hosts, _make_fake_get(table)))
    assert [svc.ip for svc in results] == ["b", "a"]


def test_scan_subnet_empty_hosts_returns_empty():
    """An empty (or all-empty) host list yields no results and calls nothing."""
    calls = []

    async def fake_get(ip):  # pragma: no cover - must never be called
        calls.append(ip)
        return {}

    results = asyncio.run(scan_subnet(["", None], fake_get))
    assert results == []
    assert calls == []


def test_scan_subnet_isolates_host_errors():
    """One failing host does not abort the whole scan."""
    async def fake_get(ip):
        if ip == "boom":
            raise TimeoutError("slow host")
        return {"Vendor": "Dell"}

    results = asyncio.run(scan_subnet(["boom", "ok"], fake_get))
    assert [svc.ip for svc in results] == ["ok"]


def test_scan_subnet_rejects_bad_concurrency():
    """A concurrency below 1 is a programming error and raises."""
    async def fake_get(ip):  # pragma: no cover - never reached
        return {}

    with pytest.raises(ValueError):
        asyncio.run(scan_subnet(["x"], fake_get, concurrency=0))


def test_scan_subnet_bounds_concurrency():
    """No more than ``concurrency`` probes run at once."""
    in_flight = 0
    max_in_flight = 0

    async def fake_get(ip):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)  # yield so others can start
        in_flight -= 1
        return {"Vendor": "Dell"}

    hosts = [f"10.0.0.{i}" for i in range(20)]
    asyncio.run(scan_subnet(hosts, fake_get, concurrency=3))
    assert max_in_flight <= 3


# --------------------------------------------------------------------------- #
# Rendering / entry point
# --------------------------------------------------------------------------- #

def test_render_table_plain_text(monkeypatch):
    """Plain-text rendering lists each service; no rich, non-TTY target."""
    services = [
        DiscoveredService("10.0.0.1", "dell", "iDRAC", "1.11.0"),
        DiscoveredService("10.0.0.2", "generic", None, None),
    ]
    buf = io.StringIO()  # StringIO has no isatty -> plain path
    discover_cli.render_table(services, stream=buf)
    text = buf.getvalue()
    assert "10.0.0.1" in text and "dell" in text and "iDRAC" in text
    assert "10.0.0.2" in text and "generic" in text
    # Missing product/version render as a dash, not "None".
    assert "None" not in text
    assert "-" in text


def test_render_table_empty(monkeypatch):
    """An empty result set prints a clear notice."""
    buf = io.StringIO()
    discover_cli.render_table([], stream=buf)
    assert "No Redfish services discovered." in buf.getvalue()


def test_main_no_hosts_returns_2():
    """Invoking with no hosts is a no-op that exits with code 2."""
    assert discover_cli.redfish_discover_main([]) == 2


def test_main_with_default_fetcher_discovers_nothing(capsys):
    """The default fetcher performs no I/O, so a scan finds nothing (exit 0)."""
    rc = discover_cli.redfish_discover_main(["10.0.0.5"])
    assert rc == 0
    assert "No Redfish services discovered." in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# HTTP fetcher factory (make_http_fetcher) — requests.get is monkeypatched, so
# no socket is ever opened.
# --------------------------------------------------------------------------- #

class _FakeResponse:
    """Stand-in for ``requests.Response`` for the fetcher tests."""

    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no JSON body")
        return self._payload


def _patch_requests_get(monkeypatch, response=None, exc=None):
    """Patch ``discover_cli.requests.get`` and record the call args.

    Returns a ``calls`` list that captures ``(url, kwargs)`` for each GET so a
    test can assert the fetcher hit ``/redfish/v1/`` with the expected
    ``verify``/``timeout``.
    """
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(discover_cli.requests, "get", fake_get)
    return calls


def test_make_http_fetcher_hits_redfish_root_with_verify_and_timeout(monkeypatch):
    """The fetcher GETs https://{host}:443/redfish/v1/ with verify+timeout set."""
    root = {"Vendor": "Dell", "RedfishVersion": "1.11.0"}
    calls = _patch_requests_get(monkeypatch, response=_FakeResponse(payload=root))

    fetch = discover_cli.make_http_fetcher()
    result = asyncio.run(fetch("10.0.0.7"))

    assert result == root
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://10.0.0.7:443/redfish/v1/"
    assert kwargs["verify"] is False
    assert kwargs["timeout"] == 2.0


def test_make_http_fetcher_honours_scheme_port_verify_timeout(monkeypatch):
    """Custom scheme/port/verify/timeout flow through to the request."""
    calls = _patch_requests_get(
        monkeypatch, response=_FakeResponse(payload={"Vendor": "Hpe"})
    )

    fetch = discover_cli.make_http_fetcher(
        scheme="http", port=8000, verify_tls=True, timeout=5.5
    )
    asyncio.run(fetch("host.example"))

    url, kwargs = calls[0]
    assert url == "http://host.example:8000/redfish/v1/"
    assert kwargs["verify"] is True
    assert kwargs["timeout"] == 5.5


def test_make_http_fetcher_non_200_returns_none(monkeypatch):
    """A non-200 status means "not a usable service root" -> None."""
    _patch_requests_get(monkeypatch, response=_FakeResponse(status_code=404))
    fetch = discover_cli.make_http_fetcher()
    assert asyncio.run(fetch("10.0.0.8")) is None


def test_make_http_fetcher_non_dict_body_returns_none(monkeypatch):
    """A 200 whose body is not a JSON object is rejected as unusable."""
    _patch_requests_get(monkeypatch, response=_FakeResponse(payload=["not", "a", "dict"]))
    fetch = discover_cli.make_http_fetcher()
    assert asyncio.run(fetch("10.0.0.9")) is None


def test_make_http_fetcher_invalid_json_returns_none(monkeypatch):
    """A 200 with a non-JSON body returns None instead of raising."""
    _patch_requests_get(monkeypatch, response=_FakeResponse(raise_json=True))
    fetch = discover_cli.make_http_fetcher()
    assert asyncio.run(fetch("10.0.0.10")) is None


def test_make_http_fetcher_swallows_transport_errors(monkeypatch):
    """A connection/timeout error on one host is reported as None, not raised."""
    _patch_requests_get(monkeypatch, exc=ConnectionError("refused"))
    fetch = discover_cli.make_http_fetcher()
    assert asyncio.run(fetch("10.0.0.11")) is None


def test_make_http_fetcher_builds_without_touching_network(monkeypatch):
    """Constructing the fetcher performs no GET; I/O happens only on await."""
    calls = _patch_requests_get(monkeypatch, response=_FakeResponse(payload={}))
    discover_cli.make_http_fetcher()
    assert calls == []
