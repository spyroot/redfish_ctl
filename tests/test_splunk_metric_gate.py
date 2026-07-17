"""Offline tests for the Splunk metric visibility gate (tools/splunk_metric_gate.py)."""
import io
import json
import time

from tools import splunk_metric_gate as gate


def _env(monkeypatch, realm="us1", token="tok"):
    """Set the gate's environment inputs for a test.

    :param monkeypatch: pytest monkeypatch fixture.
    :param realm: realm value to set (empty string clears it).
    :param token: token value to set (empty string clears it).
    """
    if realm:
        monkeypatch.setenv("SPLUNK_O11Y_REALM", realm)
    else:
        monkeypatch.delenv("SPLUNK_O11Y_REALM", raising=False)
    if token:
        monkeypatch.setenv("SPLUNK_ACCESS_TOKEN", token)
    else:
        monkeypatch.delenv("SPLUNK_ACCESS_TOKEN", raising=False)


def test_gate_passes_when_all_metrics_fresh(monkeypatch, capsys):
    """Every metric present and fresh yields exit 0 and PASS lines."""
    _env(monkeypatch)
    now_ms = int(time.time() * 1000)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {"count": 3, "newest_ms": now_ms})
    rc = gate.run_gate(["hw.health", "hw.power"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("PASS") == 2
    assert "2/2 metrics visible" in out


def test_gate_fails_on_missing_metric(monkeypatch, capsys):
    """A metric with zero time series fails the gate with exit 1."""
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {"count": 0, "newest_ms": 0})
    rc = gate.run_gate(["hw.health"])
    assert rc == 1
    assert "FAIL hw.health: no time series" in capsys.readouterr().out


def test_gate_fails_on_stale_metric(monkeypatch, capsys):
    """A metric last updated outside the freshness window fails.

    Staleness matters because the gate runs right after a push — an old
    series proves history, not that today's pipeline works.
    """
    _env(monkeypatch)
    old_ms = int((time.time() - 3 * 3600) * 1000)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {"count": 1, "newest_ms": old_ms})
    rc = gate.run_gate(["hw.health", "--since-minutes", "30"])
    assert rc == 1
    assert "stale" in capsys.readouterr().out


def test_gate_fails_when_freshness_unverifiable(monkeypatch, capsys):
    """Series without any update timestamp fail instead of passing on count.

    This edge occurs when the API returns MTS metadata without timestamp
    fields; a hard gate must not report PASS when it cannot verify the push
    actually landed inside the window.
    """
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {"count": 4, "newest_ms": 0})
    rc = gate.run_gate(["hw.health"])
    assert rc == 1
    assert "freshness unverifiable" in capsys.readouterr().out


def test_gate_configuration_errors(monkeypatch, capsys):
    """Missing token or realm is a loud exit-2 configuration error."""
    _env(monkeypatch, token="")
    assert gate.run_gate(["hw.health"]) == 2
    _env(monkeypatch, realm="")
    monkeypatch.delenv("SPLUNK_O11Y_REALM", raising=False)
    assert gate.run_gate(["hw.health"]) == 2


def test_gate_query_error_counts_as_failure(monkeypatch, capsys):
    """A transport/auth error on one metric fails that metric, not the process."""
    _env(monkeypatch)

    def boom(realm, token, metric, timeout):
        """Raise a transport error for every metric lookup.

        :param realm: ignored.
        :param token: ignored.
        :param metric: ignored.
        :param timeout: ignored.
        :raises RuntimeError: always, to simulate a failed query.
        """
        raise RuntimeError("connection refused")

    monkeypatch.setattr(gate, "query_metric", boom)
    rc = gate.run_gate(["hw.health"])
    assert rc == 1
    assert "query error" in capsys.readouterr().out


def test_metrics_file_loading(tmp_path, monkeypatch, capsys):
    """--metrics-file supplies names, honoring comments and de-duplication."""
    _env(monkeypatch)
    spec = tmp_path / "gate-metrics.txt"
    spec.write_text("# core set\nhw.health\nhw.health  # dupe\nhw.power\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        gate, "query_metric",
        lambda realm, token, metric, timeout: (seen.append(metric)
                                               or {"count": 1, "newest_ms": int(time.time() * 1000)}))
    rc = gate.run_gate(["--metrics-file", str(spec)])
    assert rc == 0
    assert seen == ["hw.health", "hw.power"]


def test_default_metric_set_includes_p0_signals(monkeypatch):
    """The built-in list carries the P0 health/state and link-down-reason names."""
    for name in ("hw.component.health", "hw.fabric.link_down_reason",
                 "hw.power.edp_violation_state"):
        assert name in gate.DEFAULT_METRICS


def test_query_metric_parses_api_response(monkeypatch):
    """query_metric extracts count and the newest update stamp from the API JSON."""
    payload = {"count": 2, "results": [
        {"lastUpdated": 1000}, {"lastUpdated": 5000, "created": 100}]}

    class FakeResponse(io.BytesIO):
        """Minimal context-manager response wrapping the canned JSON body."""

        def __enter__(self):
            """Return self as the context object.

            :return: this fake response.
            """
            return self

        def __exit__(self, *exc):
            """Close without suppressing exceptions.

            :param exc: exception triple from the with-block.
            :return: False so exceptions propagate.
            """
            return False

    captured = {}

    def fake_urlopen(request, timeout=None):
        """Capture the request and return the canned response.

        :param request: the urllib Request being opened.
        :param timeout: HTTP timeout passed through by the caller.
        :return: a FakeResponse with the canned JSON body.
        """
        captured["url"] = request.full_url
        captured["token"] = request.get_header("X-sf-token")
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    info = gate.query_metric("us1", "tok", "hw.health", 5.0)
    assert info == {"count": 2, "newest_ms": 5000}
    assert "api.us1.signalfx.com" in captured["url"]
    assert "hw.health" in captured["url"]
    assert captured["token"] == "tok"
