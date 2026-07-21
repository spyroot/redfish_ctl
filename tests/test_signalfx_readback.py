"""Tests for the SignalFx readback gate (issue #363).

A SignalFx POST returns 200/OK even when datapoints are dropped, so ingest
success is confirmed by reading the metric time series back from Splunk MTS
rather than trusting the POST status. These tests cover the readback query and
the compact canary verdict offline (the MTS HTTP call is mocked).

Author Mus spyroot@gmail.com
"""
import json
from unittest import mock

from redfish_ctl.telemetry.exporter import (
    build_readback_result,
    signalfx_metric_readback,
    verify_signalfx_readback,
)


class _FakeResp:
    """Minimal urlopen context-manager double."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        """Return the encoded body."""
        return self._payload

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
    assert out == {"count": 2, "newest_ms": 1700000009000}


def test_readback_zero_when_not_ingested():
    """The #363 case: POST succeeded but MTS shows no series -> count 0."""
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp({"count": 0, "results": []})):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out == {"count": 0, "newest_ms": 0}


def test_verify_readback_covers_each_metric():
    """verify_signalfx_readback returns a per-metric readback for every name."""
    with mock.patch("urllib.request.urlopen", return_value=_FakeResp({"count": 1, "results": [{"created": 5}]})):
        out = verify_signalfx_readback("us1", "tok", ["hw.power", "hw.temperature"])
    assert set(out) == {"hw.power", "hw.temperature"}
    assert all(v["count"] == 1 for v in out.values())


def test_verdict_ok_when_all_visible():
    """All pushed metrics visible -> no error, compact summary populated."""
    readback = {"hw.power": {"count": 1, "newest_ms": 9}, "hw.temp": {"count": 3, "newest_ms": 9}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.signalfx.com/v2/datapoint", 5,
        ["hw.power", "hw.temp"], readback, {"scrape": 10, "push": 20, "readback": 30})
    assert error is None
    assert summary["metrics_visible"] == 2 and summary["missing_metrics"] == []
    assert "readback" not in summary["timing_ms"] or summary["timing_ms"]["readback"] == 30


def test_verdict_errors_when_a_metric_is_missing():
    """POST 200 but a metric has no series -> verdict is an error (issue #363).

    This is the false-success the gate exists to catch: a 200 is not proof.
    """
    readback = {"hw.power": {"count": 1, "newest_ms": 9}, "hw.temp": {"count": 0, "newest_ms": 0}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 5,
        ["hw.power", "hw.temp"], readback, {"scrape": 10, "push": 20, "readback": 30})
    assert error is not None
    assert "not ingested" in error
    assert summary["missing_metrics"] == ["hw.temp"]
    assert summary["push_status"] == 200
