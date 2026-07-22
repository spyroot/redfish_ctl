"""Tests for the SignalFx readback gate (issue #363).

A SignalFx POST returns 200/OK even when datapoints are dropped, so ingest
success is confirmed by reading the metric time series back from Splunk MTS
rather than trusting the POST status. These tests cover the readback query and
the compact canary verdict offline (the MTS HTTP call is mocked).

Author Mus spyroot@gmail.com
"""
import json
from unittest import mock

import pytest

from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    build_readback_result,
    common_sample_dimensions,
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
    with mock.patch.object(exporter, "_open_signalfx_request", return_value=_FakeResp(payload)):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out == {"count": 2, "newest_ms": 1700000009000}


def test_readback_zero_when_not_ingested():
    """The #363 case: POST succeeded but MTS shows no series -> count 0."""
    with mock.patch.object(exporter, "_open_signalfx_request",
                           return_value=_FakeResp({"count": 0, "results": []})):
        out = signalfx_metric_readback("us1", "tok", "hw.power")
    assert out == {"count": 0, "newest_ms": 0}


def test_verify_readback_covers_each_metric():
    """verify_signalfx_readback returns a per-metric readback for every name."""
    with mock.patch.object(
            exporter, "_open_signalfx_request",
            return_value=_FakeResp({"count": 1, "results": [{"created": 5}]})):
        out = verify_signalfx_readback("us1", "tok", ["hw.power", "hw.temperature"])
    assert set(out) == {"hw.power", "hw.temperature"}
    assert all(v["count"] == 1 for v in out.values())


def test_readback_uses_no_redirect_token_request():
    """MTS readback sends the token through the redirect-disabled request helper."""
    captured = []

    def refuse_redirect(request, timeout):
        captured.append((request, timeout))
        raise ValueError("SignalFx request refused redirect")

    with mock.patch.object(exporter, "_open_signalfx_request",
                           side_effect=refuse_redirect):
        with pytest.raises(ValueError, match="refused redirect"):
            signalfx_metric_readback("us1", "api-token", "hw.power")

    assert len(captured) == 1
    request, timeout = captured[0]
    assert timeout == 20.0
    assert request.full_url.startswith("https://api.us1.signalfx.com/v2/metrictimeseries?")
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-sf-token"] == "api-token"


def test_readback_scopes_query_by_dimension():
    """The MTS query is scoped by the entity dimension so only this host's series
    is read back, not every host reporting the metric (Splunk MTS identity)."""
    assert exporter._mts_query("hw.power", {"host.name": "slot1"}) == (
        'sf_metric:"hw.power" AND host.name:"slot1"')
    assert exporter._mts_query("hw.power") == 'sf_metric:"hw.power"'


def test_readback_query_escapes_quotes_and_backslashes():
    """Dimension values are escaped before they enter the MTS query language."""
    query = exporter._mts_query(
        'hw."power"',
        {"host.name": r'slot\"1'},
    )
    assert query == 'sf_metric:"hw.\\"power\\"" AND host.name:"slot\\\\\\"1"'


def test_common_sample_dimensions_keep_deployment_join_keys():
    """Readback scopes by fixed join dimensions and drops metric-specific ones."""
    dims = {
        "host.name": "gb300-poc1-slot9",
        "node": "slot9",
        "server.address": "172.25.230.49",
        "bmc.ip": "172.25.230.29",
        "vendor": "supermicro",
        "deployment.environment": "nv72-gb300",
        "deployment.environment.name": "nv72-gb300",
    }
    samples = [
        MetricSample("hw.power", 1, dims | {"source": "environment"}),
        MetricSample("hw.temperature", 2, dims | {"source": "sensor"}),
    ]

    common = common_sample_dimensions(samples)

    assert common["deployment.environment.name"] == "nv72-gb300"
    assert common["host.name"] == "gb300-poc1-slot9"
    assert "source" not in common


_NOW = 1_700_000_000_000


def test_verdict_ok_when_series_fresh():
    """All pushed metrics have a fresh series -> no error."""
    readback = {"hw.power": {"count": 1, "newest_ms": _NOW - 1000},
                "hw.temp": {"count": 3, "newest_ms": _NOW - 2000}}
    summary, error = build_readback_result(
        200, "https://ingest.us1.observability.splunkcloud.com/v2/datapoint", 5,
        ["hw.power", "hw.temp"], readback, {"scrape": 10, "push": 20, "readback": 30}, _NOW)
    assert error is None
    assert summary["metrics_fresh"] == 2 and summary["missing_metrics"] == []


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
