"""Offline unit tests for the fleet-status consumer's pure renderers.

The consumer (``k8s/consumer/fleet_status_app.py``) turns the RedfishEndpoint
``.status`` the controller writes into JSON, Prometheus metrics, and an HTML
dashboard. These tests pin that pure shaping logic with no Kubernetes client and
no cluster: the module keeps its ``kubernetes`` import lazy, so loading it here
exercises only the offline helpers.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSUMER_MODULE = REPO_ROOT / "k8s" / "consumer" / "fleet_status_app.py"


def _load_consumer_module():
    spec = importlib.util.spec_from_file_location("fleet_status_app", CONSUMER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def app():
    """The loaded consumer module (pure helpers only)."""
    return _load_consumer_module()


def _endpoint(name, address, status=None, port=443, insecure=True):
    obj = {
        "metadata": {"name": name, "namespace": "redfish-sandbox"},
        "spec": {"address": address, "port": port, "insecure": insecure},
    }
    if status is not None:
        obj["status"] = status
    return obj


def test_normalize_ready_endpoint(app):
    """A fully-polled endpoint maps to a Ready row with typed readings."""
    obj = _endpoint(
        "gb300-n08-live",
        "https://172.25.230.28",
        status={
            "powerState": "On",
            "health": "OK",
            "temperature": {"count": 56, "maxCelsius": 41.5},
            "lastPolled": "2026-07-13T10:00:00Z",
        },
    )
    row = app.normalize_endpoint(obj)
    assert row["name"] == "gb300-n08-live"
    assert row["state"] == "Ready"
    assert row["powerState"] == "On"
    assert row["health"] == "OK"
    assert row["temperatureCount"] == 56
    assert row["temperatureMaxCelsius"] == 41.5
    assert row["backend"] == "live"


def test_normalize_pending_when_status_absent(app):
    """An endpoint the controller has not polled yet is Pending, not an error."""
    row = app.normalize_endpoint(_endpoint("fresh", "https://10.0.0.9"))
    assert row["state"] == "Pending"
    assert row["powerState"] is None
    assert row["health"] is None
    assert row["temperatureMaxCelsius"] is None


def test_normalize_pending_when_status_empty(app):
    """An empty status object still reads as Pending (no keys populated)."""
    row = app.normalize_endpoint(_endpoint("fresh", "https://10.0.0.9", status={}))
    assert row["state"] == "Pending"


def test_backend_inference_mock_vs_live(app):
    """A cluster-local Service address is a mock backend; anything else is live."""
    mock = app.normalize_endpoint(
        _endpoint("m", "http://mock-bmc.redfish-sandbox.svc.cluster.local", port=80)
    )
    live = app.normalize_endpoint(_endpoint("l", "https://172.25.230.28"))
    assert mock["backend"] == "mock"
    assert live["backend"] == "live"


def test_string_reading_is_coerced_and_bool_rejected(app):
    """Redfish readings arrive as strings; bools must never count as numbers."""
    coerced = app.normalize_endpoint(
        _endpoint("s", "https://x", status={"temperature": {"count": 1, "maxCelsius": "39.0"}})
    )
    assert coerced["temperatureMaxCelsius"] == 39.0
    rejected = app.normalize_endpoint(
        _endpoint("b", "https://x", status={"temperature": {"count": 1, "maxCelsius": True}})
    )
    assert rejected["temperatureMaxCelsius"] is None


def test_fleet_summary_counts(app):
    """Fleet summary tallies power, health, and readiness across nodes."""
    nodes = [
        app.normalize_endpoint(
            _endpoint("a", "https://1", status={"powerState": "On", "health": "OK",
                                                "temperature": {"count": 2, "maxCelsius": 30}})
        ),
        app.normalize_endpoint(
            _endpoint("b", "https://2", status={"powerState": "Off", "health": "Critical",
                                                "temperature": {"count": 0}})
        ),
        app.normalize_endpoint(_endpoint("c", "https://3")),  # pending
    ]
    summary = app.fleet_summary(nodes)
    assert summary["total"] == 3
    assert summary["poweredOn"] == 1
    assert summary["poweredOff"] == 1
    assert summary["healthy"] == 1
    assert summary["critical"] == 1
    assert summary["pending"] == 1
    assert summary["ready"] == 2


def test_render_fleet_json_shape(app):
    """The /api/nodes payload carries a summary plus the node list."""
    nodes = [app.normalize_endpoint(_endpoint("a", "https://1",
                                              status={"powerState": "On", "health": "OK"}))]
    payload = app.render_fleet_json(nodes)
    assert set(payload) == {"summary", "nodes"}
    assert payload["summary"]["total"] == 1
    assert payload["nodes"][0]["name"] == "a"


def test_render_metrics_lines(app):
    """Metrics expose power/health/temperature gauges keyed by node."""
    nodes = [
        app.normalize_endpoint(
            _endpoint("gb300-n08", "https://172.25.230.28",
                      status={"powerState": "On", "health": "OK",
                              "temperature": {"count": 56, "maxCelsius": 41.5},
                              "lastPolled": "2026-07-13T10:00:00Z"})
        ),
        app.normalize_endpoint(
            _endpoint("gb300-n09", "https://172.25.230.29",
                      status={"powerState": "Off", "health": "Critical",
                              "temperature": {"count": 0}})
        ),
    ]
    text = app.render_metrics(nodes)
    assert 'redfish_endpoint_power_on{node="gb300-n08"} 1' in text
    assert 'redfish_endpoint_power_on{node="gb300-n09"} 0' in text
    assert 'redfish_endpoint_health{node="gb300-n08"} 0' in text
    assert 'redfish_endpoint_health{node="gb300-n09"} 2' in text
    assert 'redfish_endpoint_temperature_max_celsius{node="gb300-n08"} 41.5' in text
    # No max-temp line when maxCelsius is absent.
    assert 'redfish_endpoint_temperature_max_celsius{node="gb300-n09"}' not in text
    assert text.endswith("\n")


def test_render_metrics_escapes_label_values(app):
    """A quote or backslash in an address must not break the metrics format."""
    node = app.normalize_endpoint(_endpoint('weird"node', 'https://ok',
                                            status={"powerState": "On"}))
    text = app.render_metrics([node])
    assert '\\"' in text  # the embedded quote is escaped


def test_render_html_contains_nodes_and_is_escaped(app):
    """The dashboard lists node names and escapes hostile metadata."""
    nodes = [
        app.normalize_endpoint(_endpoint("gb300-n08", "https://172.25.230.28",
                                        status={"powerState": "On", "health": "OK"})),
        app.normalize_endpoint(_endpoint("<script>x", "https://evil",
                                        status={"powerState": "Off", "health": "Warning"})),
    ]
    html = app.render_html(nodes)
    assert "gb300-n08" in html
    assert "<script>x" not in html  # escaped
    assert "&lt;script&gt;x" in html


def test_render_html_empty_fleet(app):
    """An empty fleet renders the placeholder row, not a broken table."""
    html = app.render_html([])
    assert "No RedfishEndpoint resources found" in html


def test_epoch_parsing(app):
    """lastPolled parses from RFC3339 Z form and tolerates junk/None."""
    from datetime import datetime, timezone

    expected = datetime(2026, 7, 13, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    assert app._epoch_from_rfc3339("2026-07-13T10:00:00Z") == pytest.approx(expected, abs=1)
    assert app._epoch_from_rfc3339(None) is None
    assert app._epoch_from_rfc3339("not-a-date") is None


# --------------------------------------------------------------------------- #
# NIC/DPU firmware surfacing (the "pull firmware for the 100GbE card" feature)
# --------------------------------------------------------------------------- #


def _nic_firmware(versions=("40.45.3048",), adapter_count=5, nic=4, dpu=1, components=None):
    comps = components if components is not None else [
        {"id": f"CX8_{i}", "deviceClass": "NIC", "version": versions[0], "updateable": True}
        for i in range(nic)
    ]
    return {
        "adapterCount": adapter_count,
        "nicCount": nic,
        "dpuCount": dpu,
        "firmwareCount": len(comps),
        "updateableCount": len(comps),
        "distinctVersions": list(versions),
        "components": comps,
    }


def test_normalize_extracts_nic_firmware(app):
    """networkFirmware in status flattens to nic counts, versions, and components."""
    obj = _endpoint(
        "gb300-n08-live",
        "https://172.25.230.28",
        status={"powerState": "On", "health": "OK", "networkFirmware": _nic_firmware()},
    )
    row = app.normalize_endpoint(obj)
    assert row["nicAdapterCount"] == 5
    assert row["nicCount"] == 4
    assert row["dpuCount"] == 1
    assert row["nicFirmwareVersions"] == ["40.45.3048"]
    assert any(c["id"] == "CX8_0" and c["version"] == "40.45.3048" for c in row["nicFirmware"])


def test_normalize_without_nic_firmware_is_empty_not_error(app):
    """A status with no networkFirmware yields empty NIC fields, not a crash."""
    row = app.normalize_endpoint(
        _endpoint("plain", "https://x", status={"powerState": "On"})
    )
    assert row["nicAdapterCount"] is None
    assert row["nicFirmwareVersions"] == []
    assert row["nicFirmware"] == []


def test_render_metrics_includes_nic_firmware(app):
    """Metrics expose per-component nic firmware info + drift + adapter count."""
    nodes = [
        app.normalize_endpoint(
            _endpoint("gb300-n08", "https://172.25.230.28",
                      status={"powerState": "On", "networkFirmware": _nic_firmware()})
        )
    ]
    text = app.render_metrics(nodes)
    assert 'redfish_node_nic_count{node="gb300-n08"} 5' in text
    assert 'redfish_node_nic_firmware_distinct_versions{node="gb300-n08"} 1' in text
    assert 'nic_id="CX8_0"' in text and 'version="40.45.3048"' in text
    assert "redfish_nic_firmware_info{" in text


def test_render_metrics_per_node_distinct_versions_is_not_drift(app):
    """A node with a NIC (40.x) and a DPU (32.x) reports 2 distinct versions but
    contributes ZERO fleet drift — the two are different components, not drift."""
    comps = [
        {"id": "CX8_0", "deviceClass": "NIC", "version": "40.45.3048", "updateable": True},
        {"id": "NIC_1", "deviceClass": "NIC", "version": "32.44.1600", "updateable": True},
    ]
    node = app.normalize_endpoint(
        _endpoint("n", "https://x", status={"powerState": "On",
                  "networkFirmware": _nic_firmware(versions=("40.45.3048", "32.44.1600"),
                                                   components=comps)})
    )
    text = app.render_metrics([node])
    assert 'redfish_node_nic_firmware_distinct_versions{node="n"} 2' in text
    assert "redfish_fleet_nic_firmware_drift_components{} 0" in text


def test_fleet_firmware_drift_is_cross_node_per_component(app):
    """Drift is the same component id differing across nodes; a healthy fleet is 0."""
    def node(name, cx8_ver):
        comps = [
            {"id": "CX8_0", "deviceClass": "NIC", "version": cx8_ver, "updateable": True},
            {"id": "NIC_1", "deviceClass": "NIC", "version": "32.44.1600", "updateable": True},
        ]
        return app.normalize_endpoint(_endpoint(name, "https://" + name,
            status={"powerState": "On", "networkFirmware": _nic_firmware(components=comps)}))

    healthy = [node("a", "40.45.3048"), node("b", "40.45.3048")]
    assert app.fleet_firmware_drift(healthy) == {}

    drifting = [node("a", "40.45.3048"), node("b", "40.44.0000")]
    drift = app.fleet_firmware_drift(drifting)
    assert drift == {"CX8_0": ["40.44.0000", "40.45.3048"]}
    # The fleet metric reflects the drifting component.
    assert "redfish_fleet_nic_firmware_drift_components{} 1" in app.render_metrics(drifting)


def test_metric_line_escapes_newline_and_cr(app):
    """A newline in a label value is escaped so it cannot split the metric line."""
    line = app._metric_line("m", {"node": "n", "address": "https://a\nb\rc"}, 1)
    assert "\n" not in line.rstrip("\n").replace("\\n", "")
    assert "\\n" in line and "\\r" in line


def test_temperature_count_coerces_string_and_rejects_bool(app):
    """temperatureCount is coerced from strings and never a bool (metrics-safe)."""
    coerced = app.normalize_endpoint(
        _endpoint("s", "https://x", status={"temperature": {"count": "56"}})
    )
    assert coerced["temperatureCount"] == 56
    rejected = app.normalize_endpoint(
        _endpoint("b", "https://x", status={"temperature": {"count": True}})
    )
    assert rejected["temperatureCount"] is None
    # And the metric never emits the literal True.
    assert "True" not in app.render_metrics([rejected])


def test_find_node(app):
    """find_node returns the matching row or None (backs /api/nodes/<name>)."""
    nodes = [{"name": "a"}, {"name": "b"}]
    assert app.find_node(nodes, "b") == {"name": "b"}
    assert app.find_node(nodes, "missing") is None


def test_pending_node_power_cell_is_unknown_not_off(app):
    """An unpolled (null power) node renders the neutral 'unknown' pill, not red 'off'."""
    html = app.render_html([app.normalize_endpoint(_endpoint("fresh", "https://x"))])
    assert 'class="pill unknown">—' in html
    assert 'class="pill off">—' not in html


def test_render_html_shows_nic_firmware_no_drift_for_healthy_fleet(app):
    """A fleet where every node runs the same per-component firmware shows no drift."""
    def node(name):
        comps = [{"id": "CX8_0", "deviceClass": "NIC", "version": "40.45.3048", "updateable": True}]
        return app.normalize_endpoint(_endpoint(name, "https://" + name,
            status={"powerState": "On", "networkFirmware": _nic_firmware(components=comps)}))
    html = app.render_html([node("a"), node("b")])
    assert "40.45.3048" in html
    assert "NIC Firmware" in html
    assert "fw drift" not in html  # identical firmware across the fleet = not drift


def test_render_html_flags_drifting_component_across_fleet(app):
    """When one node's CX8_0 differs from the fleet, the node cell is drift-flagged."""
    def node(name, ver):
        comps = [{"id": "CX8_0", "deviceClass": "NIC", "version": ver, "updateable": True}]
        return app.normalize_endpoint(_endpoint(name, "https://" + name,
            status={"powerState": "On", "networkFirmware": _nic_firmware(components=comps)}))
    html = app.render_html([node("a", "40.45.3048"), node("b", "40.44.0000")])
    assert "fw drift" in html  # the fleet disagrees on CX8_0
    assert "NIC FW Drift" in html  # the summary card


# --------------------------------------------------------------------------- #
# HTTP handler: routes + status codes (loopback server; k8s access stubbed out).
# --------------------------------------------------------------------------- #


@contextmanager
def _serve_consumer(app):
    """Run the consumer on an ephemeral loopback port (caller stubs _nodes)."""
    httpd = app.ThreadingHTTPServer(("127.0.0.1", 0), app._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _req(url):
    """Return (status, body-bytes), treating a 4xx/5xx as a normal response."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_routes_serve_status_and_metrics(app, monkeypatch):
    """/, /api/nodes, /api/nodes/<name>, /metrics, /healthz serve 200; unknown/missing 404.

    ``_nodes`` is stubbed so the handler never touches a Kubernetes API server.
    """
    nodes = [app.normalize_endpoint(
        _endpoint("n1", "https://1", status={"powerState": "On", "health": "OK"})
    )]
    monkeypatch.setattr(app._Handler, "_nodes", lambda self: nodes)
    with _serve_consumer(app) as base:
        assert _req(base + "/healthz")[0] == 200
        assert _req(base + "/")[0] == 200
        code, body = _req(base + "/api/nodes")
        assert code == 200 and json.loads(body)["summary"]["total"] == 1
        assert _req(base + "/api/nodes/n1")[0] == 200
        assert _req(base + "/api/nodes/missing")[0] == 404  # unknown node
        assert _req(base + "/metrics")[0] == 200
        assert _req(base + "/nope")[0] == 404  # unknown path


def test_http_backend_error_is_503(app, monkeypatch):
    """When the k8s-facing load fails, the handler returns 503, not a stack trace."""
    def boom(self):
        raise RuntimeError("cluster unreachable")
    monkeypatch.setattr(app._Handler, "_nodes", boom)
    with _serve_consumer(app) as base:
        code, body = _req(base + "/api/nodes")
        assert code == 503
        assert json.loads(body)["error"] == "backend unavailable"
