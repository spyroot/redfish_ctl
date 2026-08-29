"""Offline tests for the Splunk metric visibility gate (tools/splunk_metric_gate.py)."""
import io
import json

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
    # Containers bake an API-scoped token too; tests control it explicitly so
    # the config-error paths stay deterministic on the fleet.
    monkeypatch.delenv("SPLUNK_API_TOKEN", raising=False)


def test_gate_passes_when_all_metrics_are_active(monkeypatch, capsys):
    """Every metric with an active series yields exit 0 and FLOWING lines."""
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {
                            "count": 3, "active": 1, "active_complete": True,
                            "newest_ms": 0})
    rc = gate.run_gate(["hw.health", "hw.power"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("PASS") == 2
    assert "FLOWING=2" in out


def test_gate_fails_on_missing_metric(monkeypatch, capsys):
    """A metric with zero time series fails the gate with exit 1."""
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {
                            "count": 0, "active": 0, "active_complete": False,
                            "newest_ms": 0})
    rc = gate.run_gate(["hw.health"])
    assert rc == 1
    assert "FAIL MISSING hw.health" in capsys.readouterr().out


def test_gate_fails_when_metric_has_no_active_series(monkeypatch, capsys):
    """An existing metric with no active series fails.

    Existence proves history; Splunk's active flag proves current flow.
    """
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {
                            "count": 1, "active": 0, "active_complete": True,
                            "newest_ms": 0})
    rc = gate.run_gate(["hw.health", "--since-minutes", "30"])
    assert rc == 1
    assert "FAIL INACTIVE" in capsys.readouterr().out


def test_gate_fails_when_active_flag_is_unverifiable(monkeypatch, capsys):
    """Series without complete boolean active flags fail closed.

    The metric-time-series endpoint may omit update timestamps legitimately,
    but an absent or malformed active flag cannot establish current flow.
    """
    _env(monkeypatch)
    monkeypatch.setattr(gate, "query_metric",
                        lambda realm, token, metric, timeout: {
                            "count": 4, "active": 0, "active_complete": False,
                            "newest_ms": 0})
    rc = gate.run_gate(["hw.health"])
    assert rc == 1
    assert "active flag missing or malformed" in capsys.readouterr().out


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
                                               or {"count": 1, "active": 1,
                                                   "active_complete": True,
                                                   "newest_ms": 0}))
    rc = gate.run_gate(["--metrics-file", str(spec)])
    assert rc == 0
    assert seen == ["hw.health", "hw.power"]


def test_gate_prefers_api_token_for_queries(monkeypatch, capsys):
    """With the default token env, SPLUNK_API_TOKEN wins over the ingest token.

    Splunk separates token scopes; querying with an ingest-scoped token gets
    401s, so the gate must pick the API token when both are present.
    """
    _env(monkeypatch)
    monkeypatch.setenv("SPLUNK_API_TOKEN", "api-tok")
    seen = {}

    def record(realm, token, metric, timeout):
        """Record the token used and return a fresh series.

        :param realm: ignored.
        :param token: captured for the assertion.
        :param metric: ignored.
        :param timeout: ignored.
        :return: a fresh single-series result.
        """
        seen["token"] = token
        return {"count": 1, "active": 1, "active_complete": True,
                "newest_ms": 0}

    monkeypatch.setattr(gate, "query_metric", record)
    assert gate.run_gate(["hw.component.health"]) == 0
    assert seen["token"] == "api-tok"


def test_token_env_is_limited_to_registered_names():
    """The gate cannot dynamically read an undeclared environment variable."""
    parser = gate.build_parser()
    try:
        parser.parse_args([
            "hw.component.health",
            "--token-env",
            "UNREGISTERED_TOKEN",
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unregistered token environment name was accepted")


def test_default_metric_set_includes_p0_signals(monkeypatch):
    """The built-in list carries the P0 health/state and link-down-reason names."""
    for name in ("hw.component.health", "hw.fabric.link_down_reason",
                 "hw.power.edp_violation_state"):
        assert name in gate.DEFAULT_METRICS


def test_query_metric_parses_active_without_update_timestamps(monkeypatch):
    """query_metric treats complete boolean active flags as authoritative."""
    payload = {"count": 2, "results": [
        {"active": True}, {"active": False}]}

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
    assert info == {
        "count": 2,
        "active": 1,
        "active_complete": True,
        "newest_ms": 0,
    }
    assert "api.us1.signalfx.com" in captured["url"]
    assert "hw.health" in captured["url"]
    assert captured["token"] == "tok"


def test_query_metric_marks_non_boolean_active_as_incomplete(monkeypatch):
    """A truthy non-boolean active value cannot make the gate pass."""
    payload = {"count": 1, "results": [{"active": 1}]}

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

    monkeypatch.setattr(
        gate.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(json.dumps(payload).encode()),
    )
    info = gate.query_metric("us1", "tok", "hw.health", 5.0)
    assert info["active"] == 0
    assert info["active_complete"] is False
    assert gate.classify_liveness(info) == "ERROR"
