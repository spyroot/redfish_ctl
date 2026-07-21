"""Tests for the SignalFx readback gate (issue #363).

A SignalFx POST returns 200/OK even when datapoints are dropped, so ingest
success is confirmed by reading the metric time series back from Splunk MTS
rather than trusting the POST status. These tests cover the readback query and
the compact canary verdict offline (the MTS HTTP call is mocked).

Author Mus spyroot@gmail.com
"""
import json
from unittest import mock

from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry.exporter import (
    build_readback_result,
    signalfx_metric_readback,
    verify_signalfx_readback,
)

_NOW = 1_700_000_000_000


class _FakeResp:
    """Minimal urlopen context-manager double."""

    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        """Return the encoded body."""
        return self._payload

    def getheader(self, name, default=None):
        """Return a fake response header."""
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_readback_counts_visible_series():
    """A metric with time series in MTS reports count>0 and the newest stamp."""
    payload = {"count": 2, "results": [
        {"lastUpdated": 1700000000000}, {"lastUpdated": 1700000009000}]}
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out == {"count": 2, "newest_ms": 1700000009000, "server_ms": 0}


def test_readback_records_server_date_header():
    """The MTS HTTP Date header is captured as the readback server clock."""
    payload = {"count": 1, "results": [{"lastUpdated": _NOW}]}
    headers = {"Date": "Tue, 14 Nov 2023 22:13:20 GMT"}
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload, headers)):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out["server_ms"] == _NOW


def test_readback_zero_when_not_ingested():
    """The #363 case: POST succeeded but MTS shows no series -> count 0."""
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp({"count": 0, "results": []})):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out == {"count": 0, "newest_ms": 0, "server_ms": 0}


def test_verify_readback_covers_each_metric():
    """verify_signalfx_readback returns a per-metric readback for every name."""
    payload = {"count": 1, "results": [{"created": 5}]}
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        out = verify_signalfx_readback("us1", "tok", ["hw.power", "hw.temperature"])
    assert set(out) == {"hw.power", "hw.temperature"}
    assert all(v["count"] == 1 for v in out.values())


def test_readback_scopes_query_by_dimension():
    """The MTS query is scoped by the entity dimension so only this host's series
    is read back, not every host reporting the metric (Splunk MTS identity)."""
    assert exporter._mts_query("hw.power", {"host.name": "slot1"}) == (
        'sf_metric:"hw.power" AND host.name:"slot1"')
    assert exporter._mts_query("hw.power") == 'sf_metric:"hw.power"'


def test_verdict_ok_when_series_fresh():
    """All pushed metrics have a fresh series -> no error."""
    readback = {"hw.power": {"count": 1, "newest_ms": _NOW - 1000},
                "hw.temp": {"count": 3, "newest_ms": _NOW - 2000}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 5,
        ["hw.power", "hw.temp"], readback, {"scrape": 10, "push": 20, "readback": 30}, _NOW)
    assert error is None
    assert summary["metrics_fresh"] == 2 and summary["missing_metrics"] == []
    assert summary["clock_source"] == "caller"


def test_verdict_errors_when_a_metric_is_absent():
    """POST 200 but a metric has no series -> error (issue #363): 200 is not proof."""
    readback = {"hw.power": {"count": 1, "newest_ms": _NOW},
                "hw.temp": {"count": 0, "newest_ms": 0}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 5,
        ["hw.power", "hw.temp"], readback, {"scrape": 10, "push": 20, "readback": 30}, _NOW)
    assert error is not None and "not ingested" in error
    assert summary["missing_metrics"] == ["hw.temp"]


def test_verdict_errors_on_stale_series():
    """A series with count>0 but a STALE newest_ms is not proof of this push —
    Splunk retains inactive MTS for 13 months, so freshness is required."""
    readback = {"hw.power": {"count": 1, "newest_ms": _NOW - 40 * 24 * 3600 * 1000}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 1,
        ["hw.power"], readback, {"scrape": 10, "push": 20, "readback": 30}, _NOW)
    assert error is not None
    assert summary["metrics_fresh"] == 0 and summary["missing_metrics"] == ["hw.power"]


def test_verdict_uses_readback_server_clock_when_now_omitted():
    """Server-clock freshness does not flip when the exporter host clock is skewed."""
    readback = {
        "hw.power": {
            "count": 1,
            "newest_ms": _NOW,
            "server_ms": _NOW + 1000,
        }
    }
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 1,
        ["hw.power"], readback, {"scrape": 10, "push": 20, "readback": 30})
    assert error is None
    assert summary["clock_source"] == "signalfx_http_date"
    assert summary["readback_now_ms"] == _NOW + 1000
    assert summary["metrics_fresh"] == 1


def test_verdict_fails_closed_without_server_or_caller_clock():
    """Freshness cannot be proven if neither caller nor API server time exists."""
    readback = {"hw.power": {"count": 1, "newest_ms": _NOW, "server_ms": 0}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 1,
        ["hw.power"], readback, {"scrape": 10, "push": 20, "readback": 30})
    assert error is not None and "server time" in error
    assert summary["clock_source"] == "unavailable"
    assert summary["missing_metrics"] == ["hw.power"]


def test_verdict_freshness_window_is_configurable():
    """A wider freshness window can be requested without changing the clock source."""
    readback = {
        "hw.power": {
            "count": 1,
            "newest_ms": _NOW - 1200,
            "server_ms": _NOW,
        }
    }
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 1,
        ["hw.power"], readback, {"scrape": 10, "push": 20, "readback": 30},
        freshness_ms=1500)
    assert error is None
    assert summary["freshness_ms"] == 1500
