"""Offline tests for the Redfish telemetry exporter contract."""

import argparse
import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

import redfish_ctl.telemetry.exporter as exporter_mod
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    _require_datapoint_url,
    apply_exporter_env_file,
    build_identity_dimensions,
    build_metric_samples,
    exporter_argv_uses_secret,
    load_exporter_env_file,
    render_prometheus_text,
    resolve_signalfx_ingest_url,
    resolve_signalfx_token,
    to_signalfx_body,
)

REQUIRED_DIMS = {"host.name", "node", "server.address", "bmc.ip", "vendor"}
GB300_CORPUS = corpus_dir(
    Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


def _gb300_fixture_for_path(path):
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


@pytest.fixture
def gb300_exporter_manager():
    """Serve the committed GB300 crawl over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _gb300_fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


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


def test_identity_options_resolve_from_environment(monkeypatch):
    """Exporter identity math can be moved out of the GB300 default contract."""
    monkeypatch.setenv("REDFISH_EXPORTER_HOST_PREFIX", "rack-a")
    monkeypatch.setenv("REDFISH_EXPORTER_BMC_OCTET_BASE", "10")
    monkeypatch.setenv("REDFISH_EXPORTER_SERVER_OCTET_BASE", "100")
    monkeypatch.setenv("REDFISH_EXPORTER_SERVER_SUBNET", "198.51.100")

    dims = build_identity_dimensions(
        "203.0.113.29",
        vendor="dell",
        **exporter_mod.resolve_identity_options(),
    )

    assert dims == {
        "host.name": "rack-a-slot19",
        "node": "slot19",
        "server.address": "198.51.100.119",
        "bmc.ip": "203.0.113.29",
        "vendor": "dell",
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


def test_mapper_emits_thermal_subsystem_zone_temperatures():
    """ThermalSubsystem rows become granular temperature samples."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    samples = build_metric_samples(
        identity=dims,
        environment_rows=[],
        sensor_rows=[],
        nvlink_rows=[],
        metric_report_rows=[],
        thermal_rows=[
            {
                "Chassis": "Chassis_0",
                "DeviceName": "Front IO Temp",
                "PhysicalContext": "Intake",
                "ReadingCelsius": 24.437,
                "DataSourceUri": (
                    "/redfish/v1/Chassis/Chassis_0/Sensors/"
                    "Chassis_0_Front_IO_Temp_0"
                ),
            }
        ],
    )

    sample = next(sample for sample in samples if sample.metric == "hw.temperature")
    assert sample.value == 24.437
    assert sample.unit == "Cel"
    assert sample.dimensions["source"] == "thermal-subsystem"
    assert sample.dimensions["chassis"] == "Chassis_0"
    assert sample.dimensions["sensor"] == "Front_IO_Temp"
    assert sample.dimensions["zone"] == "Intake"


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


def test_metric_report_mapper_emits_gpu_processor_memory_and_temperature_samples():
    """GPU ProcessorMetrics, MemoryMetrics, and sensor rows get stable hw.gpu names."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    samples = build_metric_samples(
        identity=dims,
        environment_rows=[],
        sensor_rows=[],
        nvlink_rows=[],
        metric_report_rows=[
            {
                "Report": "HGX_ProcessorMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "ProcessorMetrics#/OperatingSpeedMHz"
                ),
                "MetricValue": "2070",
                "Timestamp": "2026-06-29T08:05:19.536+00:00",
            },
            {
                "Report": "HGX_ProcessorGPMMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "ProcessorMetrics#/Oem/Nvidia/FP32ActivityPercent"
                ),
                "MetricValue": "12.5",
                "Timestamp": "2026-06-29T08:05:19.536+00:00",
            },
            {
                "Report": "HGX_ProcessorMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/"
                    "ProcessorMetrics#/PowerLimitThrottleDuration"
                ),
                "MetricValue": "PT1M2.5S",
                "Timestamp": "2026-06-29T08:05:19.536+00:00",
            },
            {
                "Report": "HGX_MemoryMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Memory/GPU_0_DRAM_0/"
                    "MemoryMetrics#/CapacityUtilizationPercent"
                ),
                "MetricValue": "91",
                "Timestamp": "2026-06-29T08:05:06.788+00:00",
            },
            {
                "Report": "HGX_MemoryMetrics_0",
                "MetricProperty": (
                    "/redfish/v1/Systems/HGX_Baseboard_0/Memory/GPU_0_DRAM_0/"
                    "MemoryMetrics#/LifeTime/CorrectableECCErrorCount"
                ),
                "MetricValue": "4",
                "Timestamp": "2026-06-29T08:04:58.689+00:00",
            },
            {
                "Report": "HGX_PlatformEnvironmentMetrics_0",
                "MetricProperty": "/redfish/v1/Chassis/HGX_GPU_0/Sensors/HGX_GPU_0_TEMP_0",
                "MetricValue": "32.937500",
                "Timestamp": "2026-06-29T08:05:10.347+00:00",
            },
        ],
    )

    keyed = {
        (sample.metric, sample.dimensions.get("gpu"), sample.dimensions.get("property")): sample
        for sample in samples
    }
    assert keyed[("hw.gpu.clock_mhz", "GPU_0", "operating_speed")].value == 2070
    assert keyed[("hw.gpu.compute.utilization", "GPU_0", "fp32_activity")].value == 12.5
    assert keyed[("hw.gpu.throttle.duration_seconds", "GPU_0", "power_limit")].value == 62.5
    assert keyed[("hw.gpu.memory.capacity_utilization", "GPU_0", "capacity")].value == 91
    assert keyed[("hw.gpu.memory.ecc_errors", "GPU_0", "correctable")].value == 4
    assert keyed[("hw.gpu.temperature", "GPU_0", "temperature")].value == 32.9375
    assert keyed[
        ("hw.gpu.memory.capacity_utilization", "GPU_0", "capacity")
    ].dimensions["memory"] == "GPU_0_DRAM_0"
    assert all(REQUIRED_DIMS <= set(sample.dimensions) for sample in samples)


def test_leak_detection_mapper_emits_state_gauges_with_detector_dimensions():
    """LeakDetector rows become 0/1 hw.leak.state gauges per detector."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    samples = build_metric_samples(
        identity=dims,
        environment_rows=[],
        sensor_rows=[],
        nvlink_rows=[],
        metric_report_rows=[],
        leak_detection_rows=[
            {
                "Chassis": "Chassis_0",
                "Id": "Chassis_0_LeakDetector_0_ColdPlate",
                "Name": "Chassis 0 LeakDetector 0 ColdPlate",
                "DetectorState": "OK",
                "LeakDetectorType": "Moisture",
                "State": "Enabled",
                "Health": "OK",
            },
            {
                "Chassis": "Chassis_0",
                "Id": "Chassis_0_LeakDetector_1_ColdPlate",
                "Name": "Chassis 0 LeakDetector 1 ColdPlate",
                "DetectorState": "Critical",
                "LeakDetectorType": "Moisture",
                "State": "Enabled",
                "Health": "Critical",
            },
        ],
    )

    leak_samples = {sample.dimensions["detector"]: sample for sample in samples}
    ok_sample = leak_samples["Chassis_0_LeakDetector_0_ColdPlate"]
    critical_sample = leak_samples["Chassis_0_LeakDetector_1_ColdPlate"]

    assert ok_sample.metric == "hw.leak.state"
    assert ok_sample.value == 0
    assert critical_sample.value == 1
    assert ok_sample.dimensions["source"] == "leak-detector"
    assert ok_sample.dimensions["chassis"] == "Chassis_0"
    assert ok_sample.dimensions["detector_type"] == "Moisture"
    assert ok_sample.dimensions["detector_state"] == "OK"
    assert ok_sample.dimensions["health"] == "OK"
    assert REQUIRED_DIMS <= set(ok_sample.dimensions)


def test_scrape_health_samples_report_success_and_duration():
    """Each exporter scrape reports a health gauge and duration sample."""
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")

    samples = exporter_mod.scrape_health_samples(
        dims,
        ok=True,
        duration_seconds=1.25,
    )

    by_metric = {sample.metric: sample for sample in samples}
    assert by_metric["hw.scrape.ok"].value == 1
    assert by_metric["hw.scrape.ok"].dimensions["source"] == "exporter"
    assert by_metric["hw.scrape.duration_seconds"].value == pytest.approx(1.25)
    assert by_metric["hw.scrape.duration_seconds"].unit == "s"
    assert REQUIRED_DIMS <= set(by_metric["hw.scrape.ok"].dimensions)


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


def test_exporter_env_file_supports_redfish_keys(tmp_path):
    """The credential file accepts REDFISH_* keys (the going-forward names)."""
    env_file = tmp_path / "redfish_exporter.env"
    env_file.write_text(
        "\n".join([
            "REDFISH_IP=203.0.113.10",
            "REDFISH_USERNAME=admin",
            "REDFISH_PASSWORD=not-real",
            "REDFISH_PORT=443",
        ])
    )
    assert load_exporter_env_file(env_file) == {
        "REDFISH_IP": "203.0.113.10",
        "REDFISH_USERNAME": "admin",
        "REDFISH_PASSWORD": "not-real",
        "REDFISH_PORT": "443",
    }

    args = argparse.Namespace(idrac_ip="", idrac_username="root",
                              idrac_password="", idrac_port=443,
                              exporter_credential_file=str(env_file))
    apply_exporter_env_file(args)
    assert args.idrac_ip == "203.0.113.10"
    assert args.idrac_username == "admin"
    assert args.idrac_password == "not-real"
    assert args.idrac_port == 443


def test_exporter_env_file_prefers_redfish_over_idrac(tmp_path):
    """When a file carries both, REDFISH_* wins; IDRAC_* alone still works."""
    both = tmp_path / "both.env"
    both.write_text("REDFISH_IP=203.0.113.10\nIDRAC_IP=198.51.100.5\n")
    args = argparse.Namespace(idrac_ip="", idrac_username="root",
                              idrac_password="", idrac_port=443)
    apply_exporter_env_file(args, path=str(both))
    assert args.idrac_ip == "203.0.113.10"  # REDFISH_* preferred

    legacy = tmp_path / "legacy.env"
    legacy.write_text("IDRAC_IP=198.51.100.5\n")
    args2 = argparse.Namespace(idrac_ip="", idrac_username="root",
                               idrac_password="", idrac_port=443)
    apply_exporter_env_file(args2, path=str(legacy))
    assert args2.idrac_ip == "198.51.100.5"  # legacy fallback still honored


def test_exporter_config_file_flattens_signalfx_and_identity(tmp_path):
    """The exporter config spec carries SignalFx and identity settings."""
    token_file = tmp_path / "token"
    spec = tmp_path / "exporter.json"
    spec.write_text(json.dumps({
        "signalfx": {
            "ingest_url": "https://ingest.example.test/v2/datapoint",
            "token_file": str(token_file),
        },
        "identity": {
            "host_prefix": "rack-a",
            "bmc_octet_base": 20,
            "server_octet_base": 100,
            "server_subnet": "198.51.100",
        },
    }), encoding="utf-8")

    assert exporter_mod.exporter_config_options(str(spec)) == {
        "signalfx_ingest_url": "https://ingest.example.test/v2/datapoint",
        "signalfx_token_file": str(token_file),
        "identity_host_prefix": "rack-a",
        "identity_bmc_octet_base": 20,
        "identity_server_octet_base": 100,
        "identity_server_subnet": "198.51.100",
    }


def test_signalfx_token_resolves_direct_file_and_env(tmp_path, monkeypatch):
    """SignalFx token plumbing supports direct, file, and env sources."""
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("SPLUNK_ACCESS_TOKEN", "env-token")

    assert resolve_signalfx_token(token="direct-token") == "direct-token"
    assert resolve_signalfx_token(token_file=str(token_file)) == "file-token"
    assert resolve_signalfx_token() == "env-token"


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
    assert {"hw.power", "hw.gpu.power", "hw.fabric.rx_bytes", "hw.leak.state"} <= metrics
    assert {"hw.scrape.ok", "hw.scrape.duration_seconds"} <= metrics
    scrape_ok = next(point for point in gauges if point["metric"] == "hw.scrape.ok")
    scrape_duration = next(
        point for point in gauges
        if point["metric"] == "hw.scrape.duration_seconds"
    )
    assert scrape_ok["value"] == 1
    assert scrape_ok["dimensions"]["source"] == "exporter"
    assert scrape_duration["value"] >= 0
    assert scrape_duration["dimensions"]["source"] == "exporter"
    assert {
        "hw.gpu.clock_mhz",
        "hw.gpu.compute.utilization",
        "hw.gpu.memory.capacity_utilization",
        "hw.gpu.throttle.duration_seconds",
        "hw.gpu.temperature",
    } <= metrics
    leak_points = [point for point in gauges if point["metric"] == "hw.leak.state"]
    assert len(leak_points) == 4
    assert {point["value"] for point in leak_points} == {0.0}
    assert {point["dimensions"]["source"] for point in leak_points} == {"leak-detector"}
    assert {
        point["dimensions"]["detector"]
        for point in leak_points
    } == {
        "Chassis_0_LeakDetector_0_ColdPlate",
        "Chassis_0_LeakDetector_0_Manifold",
        "Chassis_0_LeakDetector_1_ColdPlate",
        "Chassis_0_LeakDetector_1_Manifold",
    }
    assert all(REQUIRED_DIMS <= set(point["dimensions"]) for point in gauges)
    thermal_points = [
        point for point in gauges
        if point["metric"] == "hw.temperature"
        and point["dimensions"].get("source") == "thermal-subsystem"
    ]
    assert thermal_points
    assert {"chassis", "sensor", "zone"} <= set(thermal_points[0]["dimensions"])
    assert all(recorded.method != "POST" for recorded in service.requests)


def test_exporter_command_uses_config_file_for_signalfx_and_identity(
    redfish_mock_factory,
    tmp_path,
    monkeypatch,
):
    """A config spec supplies SignalFx token source and identity overrides."""
    mgr, _service = redfish_mock_factory("supermicro")
    token_file = tmp_path / "signalfx-token"
    token_file.write_text("file-token\n", encoding="utf-8")
    spec = tmp_path / "exporter.json"
    spec.write_text(json.dumps({
        "signalfx": {
            "ingest_url": "https://ingest.example.test/v2/datapoint",
            "token_file": str(token_file),
        },
        "identity": {
            "host_prefix": "rack-a",
            "bmc_octet_base": 20,
            "server_octet_base": 100,
            "server_subnet": "198.51.100",
        },
    }), encoding="utf-8")

    calls = []

    def fake_push(body, token, ingest_url, timeout=20.0):
        calls.append({"body": body, "token": token, "ingest_url": ingest_url})
        return 202

    monkeypatch.delenv("SPLUNK_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SPLUNK_INGEST_URL", raising=False)
    monkeypatch.setattr(exporter_mod, "push_signalfx", fake_push)

    result = mgr.sync_invoke(
        ApiRequestType.Exporter,
        "exporter",
        once=True,
        exporter_output="signalfx",
        push_signalfx=True,
        exporter_config_file=str(spec),
        label_bmc_ip="172.25.230.29",
        vendor="supermicro",
    )

    assert result.extra["push_status"] == 202
    assert calls[0]["token"] == "file-token"
    assert calls[0]["ingest_url"] == "https://ingest.example.test/v2/datapoint"
    first_dims = calls[0]["body"]["gauge"][0]["dimensions"]
    assert first_dims["host.name"] == "rack-a-slot9"
    assert first_dims["server.address"] == "198.51.100.109"


def test_signalfx_push_loop_jitters_sleep(monkeypatch):
    """Long-running push mode offsets the poll interval to avoid scrape bursts."""
    samples = [
        MetricSample(
            metric="hw.scrape.ok",
            value=1,
            dimensions=build_identity_dimensions("172.25.230.29", vendor="supermicro")
            | {"source": "exporter"},
        )
    ]
    pushes = []

    def fake_push(body, token, ingest_url, timeout=20.0):
        pushes.append((body, token, ingest_url, timeout))
        return 200

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise RuntimeError("stop loop")

    monotonic_values = iter([100.0, 105.0])
    monkeypatch.setattr(exporter_mod, "push_signalfx", fake_push)
    monkeypatch.setattr(exporter_mod.random, "random", lambda: 1.0)
    monkeypatch.setattr(exporter_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(exporter_mod.time, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop loop"):
        exporter_mod.run_signalfx_loop(
            lambda: samples,
            "token",
            "https://ingest.us1.signalfx.com/v2/datapoint",
            interval=30.0,
            timeout=1.0,
        )

    assert len(pushes) == 1
    assert sleep_calls == [pytest.approx(28.0)]


def test_signalfx_push_loop_continues_after_transient_push_error(monkeypatch):
    """A transient SignalFx push failure is reported but does not kill the loop."""
    samples = [
        MetricSample(
            metric="hw.scrape.ok",
            value=1,
            dimensions=build_identity_dimensions("172.25.230.29", vendor="supermicro")
            | {"source": "exporter"},
        )
    ]
    push_calls = []

    def fake_push(body, token, ingest_url, timeout=20.0):
        push_calls.append((body, token, ingest_url, timeout))
        if len(push_calls) == 1:
            raise TimeoutError("temporary ingest timeout")
        return 200

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise RuntimeError("stop loop")

    errors = []
    monotonic_values = iter([100.0, 101.0, 130.0, 132.0])
    monkeypatch.setattr(exporter_mod, "push_signalfx", fake_push)
    monkeypatch.setattr(exporter_mod.random, "random", lambda: 0.5)
    monkeypatch.setattr(exporter_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(exporter_mod.time, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop loop"):
        exporter_mod.run_signalfx_loop(
            lambda: samples,
            "token",
            "https://ingest.us1.signalfx.com/v2/datapoint",
            interval=30.0,
            timeout=1.0,
            on_error=errors.append,
        )

    assert len(push_calls) == 2
    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
    assert sleep_calls == [pytest.approx(29.0), pytest.approx(28.0)]


def test_exporter_uses_environment_metrics_command_rollups(gb300_exporter_manager):
    """Exporter output includes EnvironmentMetrics rows for every GB300 resource."""
    manager, requests = gb300_exporter_manager

    result = manager.sync_invoke(
        ApiRequestType.Exporter,
        "exporter",
        once=True,
        exporter_output="signalfx",
        label_bmc_ip="172.25.230.37",
        vendor="supermicro",
    )

    gauges = result.data["gauge"]
    processor_power = [
        point for point in gauges
        if point["metric"] == "hw.gpu.power"
        and point["dimensions"].get("source") == "environment"
        and point["dimensions"].get("resource_type") == "Processor"
        and point["dimensions"].get("resource") == "GPU_0"
    ]
    memory_power = [
        point for point in gauges
        if point["metric"] == "hw.power"
        and point["dimensions"].get("source") == "environment"
        and point["dimensions"].get("resource_type") == "Memory"
        and point["dimensions"].get("resource") == "GPU_0_DRAM_0"
    ]
    processor_energy = [
        point for point in gauges
        if point["metric"] == "hw.energy_kwh"
        and point["dimensions"].get("source") == "environment"
        and point["dimensions"].get("resource_type") == "Processor"
        and point["dimensions"].get("resource") == "GPU_0"
    ]

    assert processor_power[0]["value"] == pytest.approx(231.939)
    assert memory_power[0]["value"] == pytest.approx(34.458)
    assert processor_energy[0]["value"] == pytest.approx(63.95437164505238)
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()


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


def test_resolve_signalfx_accepts_observability_host(monkeypatch):
    """ingest.<realm>.observability.splunkcloud.com/v2/datapoint is the current,
    correct Splunk ingest host — it is accepted as-is, not rewritten (the zero-
    visibility in #363 was not the host; see the readback gate)."""
    monkeypatch.delenv("SPLUNK_INGEST_URL", raising=False)
    url = "https://ingest.us1.observability.splunkcloud.com/v2/datapoint"
    assert resolve_signalfx_ingest_url(url) == url
    monkeypatch.setenv("SPLUNK_INGEST_URL",
                       "https://ingest.eu0.observability.splunkcloud.com/v2/datapoint")
    assert (resolve_signalfx_ingest_url()
            == "https://ingest.eu0.observability.splunkcloud.com/v2/datapoint")


def test_require_datapoint_url_accepts_both_hosts_needs_path():
    """Both the observability and legacy signalfx hosts are accepted with the
    /v2/datapoint path; only a bare host (no path) is rejected."""
    for host in ("ingest.us1.observability.splunkcloud.com",
                 "ingest.us1.signalfx.com"):
        full = f"https://{host}/v2/datapoint"
        assert _require_datapoint_url(full) == full
    with pytest.raises(ValueError, match="v2/datapoint"):
        _require_datapoint_url("https://ingest.us1.observability.splunkcloud.com")


def _report_row(source_property, value, report="HGX_HealthMetrics_0"):
    """Build a MetricReport row whose property path ends in source_property.

    :param source_property: dotted property (for example ``Status.Health``).
    :param value: the MetricValue string to carry.
    :param report: report name for the ``report`` dimension.
    :return: a MetricReport row dict.
    """
    suffix = source_property.replace(".", "/")
    return {"Report": report,
            "MetricProperty": f"/redfish/v1/Chassis/HGX_GPU_0#/{suffix}",
            "MetricValue": value}


def _enum_samples(rows):
    """Run MetricReport rows through the mapper with a fixed identity.

    :param rows: MetricReport rows to map.
    :return: the emitted MetricSample list.
    """
    dims = build_identity_dimensions("172.25.230.29", vendor="supermicro")
    return build_metric_samples(identity=dims, environment_rows=[], sensor_rows=[],
                                nvlink_rows=[], metric_report_rows=rows)


def test_expected_signals_contract():
    """Every fixture row in specs/telemetry/expected_signals.yaml is emitted.

    This is the M2 gap-closure gate: expected signals minus emitted signals
    must be empty, so the code can never silently drift from the contract.
    """
    import yaml
    spec = yaml.safe_load(
        (Path(__file__).parent.parent / "specs/telemetry/expected_signals.yaml")
        .read_text(encoding="utf-8"))
    missing = []
    for row in spec["signals"]:
        samples = _enum_samples([_report_row(row["source_property"], row["input"])])
        want = row["expected"]
        hit = [s for s in samples
               if s.metric == want["metric"] and s.value == float(want["value"])
               and all(s.dimensions.get(k) == v for k, v in want["labels"].items())]
        if not hit:
            missing.append(f'{row["source_property"]}={row["input"]} -> {want["metric"]}')
    assert missing == []


def test_state_allowlists_match_contract_spec():
    """Code allowlists equal the spec allowlists (G0 documentation truth)."""
    import yaml
    spec = yaml.safe_load(
        (Path(__file__).parent.parent / "specs/telemetry/expected_signals.yaml")
        .read_text(encoding="utf-8"))
    allow = spec["allowlists"]
    assert set(allow["health"]) == exporter_mod.HEALTH_LABELS | {"unknown"}
    assert set(allow["state"]) == exporter_mod.STATE_LABELS | {"unknown"}
    assert set(allow["reason"]) == exporter_mod.LINK_DOWN_REASONS | {"other"}
    assert set(allow["reset_type"]) == exporter_mod.RESET_TYPES | {"other"}


def test_one_hot_state_samples_shape():
    """State rows emit value 1 with normalized labels and full join dims."""
    samples = _enum_samples([
        _report_row("Status.Health", "Warning"),
        _report_row("Status.State", "StandbyOffline"),
        _report_row("Oem.Nvidia.LinkDownReasonCode", "PeerResetEvent",
                    report="HGX_ProcessorPortMetrics_0"),
    ])
    by_metric = {s.metric: s for s in samples if s.metric.startswith(("hw.component", "hw.fabric"))}
    health = by_metric["hw.component.health"]
    assert health.value == 1.0
    assert health.dimensions["health"] == "warning"
    assert health.dimensions["report"] == "HGX_HealthMetrics_0"
    state = by_metric["hw.component.state"]
    assert state.value == 1.0
    assert state.dimensions["state"] == "standby_offline"
    reason = by_metric["hw.fabric.link_down_reason"]
    assert reason.value == 1.0
    assert reason.dimensions["reason"] == "peer_reset_event"
    assert reason.dimensions["fabric"] == "nvlink"


def test_unknown_enum_values_map_to_unknown_or_other():
    """Vendor surprises become bounded labels — never dropped, never free-form.

    This edge occurs whenever a firmware update introduces a new enum value;
    the M1 gate requires it to surface as unknown/other so dashboards see the
    component instead of losing it.
    """
    samples = _enum_samples([
        _report_row("Status.Health", "VendorSpecialState"),
        _report_row("Oem.Nvidia.LinkDownReasonCode", "BrandNewReason!!"),
        _report_row("Oem.Nvidia.LastResetType", "WeirdReset"),
    ])
    by_metric = {s.metric: s for s in samples if not s.metric.startswith("hw.scrape")}
    assert by_metric["hw.component.health"].dimensions["health"] == "unknown"
    assert by_metric["hw.fabric.link_down_reason"].dimensions["reason"] == "other"
    assert by_metric["hw.component.last_reset_type"].dimensions["reset_type"] == "other"
    assert all(s.value == 1.0 for s in by_metric.values())
