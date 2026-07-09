"""Offline tests for the Redfish telemetry exporter contract."""

import pytest

import redfish_ctl.telemetry.exporter as exporter_mod
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    build_identity_dimensions,
    build_metric_samples,
    exporter_argv_uses_secret,
    load_exporter_env_file,
    render_prometheus_text,
    resolve_signalfx_ingest_url,
    to_signalfx_body,
)

REQUIRED_DIMS = {"host.name", "node", "server.address", "bmc.ip", "vendor"}


def test_identity_dimensions_follow_nv72_slot_contract():
    """BMC IP slot math creates the join dimensions used by nv72 dashboards."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")

    assert dims == {
        "host.name": "gb300-poc1-slot9",
        "node": "slot9",
        "server.address": "172.25.230.49",
        "bmc.ip": "172.25.230.29",
        "vendor": "supermicro",
    }


def test_mapper_emits_chassis_gpu_and_fabric_samples():
    """Normalized Redfish rows become hw.* samples with required dimensions."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    samples = build_metric_samples(
        identity=dims,
        environment_rows=[
            {
                "Chassis": "Chassis_0",
                "PowerWatts": {"Reading": 1349.263802},
                "EnergykWh": {"Reading": 12.5},
                "FanSpeedsPercent": [
                    {"DeviceName": "Chassis Fan 1", "SpeedRPM": 11843.0}
                ],
            },
            {
                "Chassis": "HGX_GPU_0",
                "PowerWatts": {"Reading": 231.958},
                "EnergykWh": {"Reading": 63.9},
            },
        ],
        sensor_rows=[
            {
                "Chassis": "Chassis_0",
                "Name": "Inlet Temp",
                "Reading": 24.0,
                "ReadingUnits": "Cel",
                "ReadingType": "Temperature",
                "Health": "OK",
            },
            {
                "Chassis": "PDB_0",
                "Name": "Input Voltage",
                "Reading": 52.0,
                "ReadingUnits": "V",
                "ReadingType": "Voltage",
                "Health": "OK",
            },
        ],
        nvlink_rows=[
            {
                "System": "HGX_Baseboard_0",
                "GPU": "GPU_0",
                "Port": "NVLink_0",
                "LinkStatus": "LinkUp",
                "CurrentSpeedGbps": 400.0,
                "RXBytes": 9460179851686,
                "TXBytes": 9386274516626,
                "BitErrorRate": 1.5e-254,
            }
        ],
        metric_report_rows=[],
    )

    by_name = {sample.metric for sample in samples}
    assert {
        "hw.power",
        "hw.energy_kwh",
        "hw.gpu.power",
        "hw.temperature",
        "hw.voltage",
        "hw.fan_speed",
        "hw.fabric.link_up",
        "hw.fabric.port_speed",
        "hw.fabric.rx_bytes",
        "hw.fabric.tx_bytes",
        "hw.fabric.bit_error_rate",
    } <= by_name
    assert all(REQUIRED_DIMS <= set(sample.dimensions) for sample in samples)


def test_metric_report_mapper_emits_nvlink_bandwidth_and_error_counters():
    """TelemetryService MetricProperty paths add per-link NVLink counters."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    samples = build_metric_samples(
        identity=dims,
        environment_rows=[],
        sensor_rows=[],
        nvlink_rows=[],
        metric_report_rows=[
            {
                "Report": "HGX_ProcessorPortMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "Ports/NVLink_0/Metrics#/Oem/Nvidia/NVLinkDataRxBandwidthGbps"
                ),
                "MetricValue": "123.5",
                "Timestamp": "2026-06-29T08:05:20.895+00:00",
            },
            {
                "Report": "HGX_ProcessorPortMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "Ports/NVLink_0/Metrics#/RXErrors"
                ),
                "MetricValue": "7",
                "Timestamp": "2026-06-29T08:05:20.895+00:00",
            },
            {
                "Report": "HGX_ProcessorPortMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "Ports/NVLink_0/Metrics#/Oem/Nvidia/FECErrorCount"
                ),
                "MetricValue": "3",
                "Timestamp": "2026-06-29T08:05:20.895+00:00",
            },
            {
                "Report": "HGX_ProcessorPortMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "Ports/NVLink_0/Metrics#/Oem/Nvidia/CRCErrorCount"
                ),
                "MetricValue": "2",
                "Timestamp": "2026-06-29T08:05:20.895+00:00",
            },
        ],
    )

    by_metric = {sample.metric: sample for sample in samples}
    assert by_metric["hw.fabric.rx_gbps"].value == 123.5
    assert by_metric["hw.fabric.rx_errors"].value == 7
    assert by_metric["hw.fabric.fec_errors"].value == 3
    assert by_metric["hw.fabric.crc_errors"].value == 2
    assert by_metric["hw.fabric.rx_gbps"].dimensions["gpu"] == "GPU_0"
    assert by_metric["hw.fabric.rx_gbps"].dimensions["port"] == "NVLink_0"


def test_prometheus_text_preserves_contract_names_and_dimensions():
    """Prometheus text output carries hw.* names and dotted OTel dimensions."""
    sample = MetricSample(
        metric="hw.power",
        value=1349.25,
        dimensions=build_identity_dimensions("172.25.230.29", vendor="supermicro")
        | {"source": "chassis"},
        metric_type="gauge",
    )

    text = render_prometheus_text([sample])

    assert "# TYPE hw.power gauge" in text
    assert "hw.power{" in text
    assert 'host.name="gb300-poc1-slot9"' in text
    assert 'server.address="172.25.230.49"' in text
    assert 'bmc.ip="172.25.230.29"' in text
    assert text.endswith("\n")


def test_signalfx_body_uses_gauge_envelope_and_dimensions():
    """SignalFx push output matches the /v2/datapoint gauge envelope."""
    sample = MetricSample(
        metric="hw.fabric.link_up",
        value=1,
        dimensions=build_identity_dimensions("172.25.230.29", vendor="supermicro")
        | {"fabric": "nvlink", "gpu": "GPU_0", "port": "NVLink_0"},
        metric_type="gauge",
    )

    body = to_signalfx_body([sample])

    assert body["gauge"][0]["metric"] == "hw.fabric.link_up"
    assert body["gauge"][0]["value"] == 1
    assert body["gauge"][0]["dimensions"]["host.name"] == "gb300-poc1-slot9"


def test_exporter_env_file_loader_and_argv_secret_guard(tmp_path):
    """Runtime files are supported, while exporter password argv is rejected."""
    env_file = tmp_path / "idrac_exporter.env"
    env_file.write_text(
        "\n".join([
            "IDRAC_IP=172.25.230.29",
            "IDRAC_USERNAME=admin",
            "IDRAC_PASSWORD=not-real",
            "IDRAC_PORT=443",
        ])
    )

    assert load_exporter_env_file(env_file) == {
        "IDRAC_IP": "172.25.230.29",
        "IDRAC_USERNAME": "admin",
        "IDRAC_PASSWORD": "not-real",
        "IDRAC_PORT": "443",
    }
    assert exporter_argv_uses_secret(["idrac_ctl", "--idrac_password", "not-real", "exporter"])
    assert exporter_argv_uses_secret(["idrac_ctl", "--idrac_password=not-real", "exporter"])
    assert not exporter_argv_uses_secret(["idrac_ctl", "exporter"])


def test_exporter_command_collects_supermicro_fixture_metrics(redfish_mock_factory):
    """The exporter scrapes the GB300 corpus offline and emits SignalFx datapoints."""
    mgr, service = redfish_mock_factory("supermicro")

    result = mgr.sync_invoke(
        ApiRequestType.Exporter,
        "exporter",
        once=True,
        exporter_output="signalfx",
        label_bmc_ip="172.25.230.29",
        vendor="supermicro",
    )

    gauges = result.data["gauge"]
    metrics = {point["metric"] for point in gauges}
    assert {"hw.power", "hw.gpu.power", "hw.fabric.rx_bytes"} <= metrics
    assert all(REQUIRED_DIMS <= set(point["dimensions"]) for point in gauges)
    assert all(recorded.method != "POST" for recorded in service.requests)


def test_once_push_signalfx_posts_body_exactly_once(redfish_mock_factory, monkeypatch):
    """--once --push-signalfx builds the SignalFx body AND POSTs it exactly once."""
    mgr, _service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("SPLUNK_ACCESS_TOKEN", "test-token")

    calls = []

    def fake_push(body, token, ingest_url, timeout=20.0):
        calls.append({"body": body, "token": token, "ingest_url": ingest_url})
        return 200

    monkeypatch.setattr(exporter_mod, "push_signalfx", fake_push)

    result = mgr.sync_invoke(
        ApiRequestType.Exporter,
        "exporter",
        once=True,
        exporter_output="signalfx",
        push_signalfx=True,
        signalfx_ingest_url="https://ingest.us1.signalfx.com/v2/datapoint",
        label_bmc_ip="172.25.230.29",
        vendor="supermicro",
    )

    # Pushed exactly once, with the same body that is returned to the caller.
    assert len(calls) == 1
    assert calls[0]["token"] == "test-token"
    assert calls[0]["ingest_url"] == "https://ingest.us1.signalfx.com/v2/datapoint"
    assert calls[0]["body"] is result.data
    assert result.data["gauge"]
    assert result.extra["push_status"] == 200
    assert result.extra["sample_count"] == len(result.data["gauge"])


def test_once_push_signalfx_rejects_bare_ingest_url(redfish_mock_factory, monkeypatch):
    """A bare host (no /v2/datapoint) is rejected before any datapoint is pushed."""
    mgr, _service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("SPLUNK_ACCESS_TOKEN", "test-token")

    called = []
    monkeypatch.setattr(exporter_mod, "push_signalfx",
                        lambda *a, **k: called.append(1))

    with pytest.raises(ValueError, match="v2/datapoint"):
        mgr.sync_invoke(
            ApiRequestType.Exporter,
            "exporter",
            once=True,
            exporter_output="signalfx",
            push_signalfx=True,
            signalfx_ingest_url="https://ingest.us1.observability.splunkcloud.com",
            label_bmc_ip="172.25.230.29",
            vendor="supermicro",
        )
    assert called == []


def test_once_push_signalfx_requires_ingest_url(redfish_mock_factory, monkeypatch):
    """Missing SPLUNK_INGEST_URL (and no --signalfx-ingest-url) raises a clear error."""
    mgr, _service = redfish_mock_factory("supermicro")
    monkeypatch.setenv("SPLUNK_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("SPLUNK_INGEST_URL", raising=False)

    with pytest.raises(ValueError, match="SPLUNK_INGEST_URL is not set"):
        mgr.sync_invoke(
            ApiRequestType.Exporter,
            "exporter",
            once=True,
            exporter_output="signalfx",
            push_signalfx=True,
            label_bmc_ip="172.25.230.29",
            vendor="supermicro",
        )


def test_resolve_signalfx_ingest_url_validates_full_datapoint_endpoint(monkeypatch):
    """The ingest URL resolver falls back to env and demands the /v2/datapoint path."""
    monkeypatch.setenv("SPLUNK_INGEST_URL",
                       "https://ingest.us1.signalfx.com/v2/datapoint")
    assert (resolve_signalfx_ingest_url()
            == "https://ingest.us1.signalfx.com/v2/datapoint")
    assert (resolve_signalfx_ingest_url("https://custom.example/v2/datapoint")
            == "https://custom.example/v2/datapoint")

    with pytest.raises(ValueError, match="v2/datapoint"):
        resolve_signalfx_ingest_url("https://ingest.us1.observability.splunkcloud.com")

    monkeypatch.delenv("SPLUNK_INGEST_URL", raising=False)
    with pytest.raises(ValueError, match="SPLUNK_INGEST_URL is not set"):
        resolve_signalfx_ingest_url()
