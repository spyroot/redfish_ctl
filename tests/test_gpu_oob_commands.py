"""Offline tests for the four out-of-band GPU/fabric commands.

All run against the full redacted-free GB300 corpus served from
tests/supermicro_fixtures/ through the real requests path, proving each command
navigates the real link graph on a non-Dell host:

- metric-definitions  -> TelemetryService/MetricReportDefinitions
- component-integrity -> ComponentIntegrity (SPDM attestation)
- network-adapters    -> Chassis/<x>/NetworkAdapters (NICs + DPUs)
- nvlink-ports        -> Systems -> GPU Processors -> NVLink Ports -> Metrics

These exercise the corpus-as-sim: no bespoke fixtures, just behavioral asserts.
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_metric_definitions_lists_report_defs(redfish_mock_factory):
    """metric-definitions returns every MetricReportDefinition with its report + count."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.MetricReportDefinitions, "metric-definitions")
    assert isinstance(result.data, list) and result.data
    assert len(result.data) == 10
    by_id = {r["Definition"]: r for r in result.data}
    gpu = by_id.get("HGX_ProcessorMetrics_0")
    assert gpu is not None
    # the definition links to its live report and publishes a metric set
    assert gpu["Report"] == "HGX_ProcessorMetrics_0"
    assert gpu["MetricCount"] > 0
    assert gpu["Type"] in ("OnRequest", "Periodic", "OnChange")


def test_component_integrity_lists_spdm(redfish_mock_factory):
    """component-integrity enumerates all 14 SPDM RoTs with cert links, no PEM."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.ComponentIntegrity, "component-integrity")
    assert isinstance(result.data, list) and result.data
    assert len(result.data) == 14
    erot = next((r for r in result.data if r["Id"] == "HGX_ERoT_BMC_0"), None)
    assert erot is not None
    assert erot["Type"] == "SPDM"
    assert erot["TargetComponentURI"]
    # cert chain is referenced by URI only — never the PEM body itself
    assert erot["CertificateURI"] == "/redfish/v1/Chassis/HGX_ERoT_BMC_0/Certificates/CertChain"
    assert "BEGIN CERTIFICATE" not in str(result.data)


def test_network_adapters_classifies_nic_and_dpu(redfish_mock_factory):
    """network-adapters finds the ConnectX NICs and BlueField DPU, classified."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.NetworkAdapters, "network-adapters")
    assert isinstance(result.data, list) and result.data
    classes = {r["DeviceClass"] for r in result.data}
    assert "NIC" in classes      # ConnectX-8
    assert "DPU" in classes      # BlueField-3
    # every adapter carries a model and lives in a named chassis
    assert all(r["Model"] and r["Chassis"] for r in result.data)


def test_nvlink_ports_reads_gpu_links(redfish_mock_factory):
    """nvlink-ports walks GPU NVLink ports and pulls per-port metrics.

    The crawl captured GPU_0's 18 NVLink ports fully; ports whose Metrics leaf
    was not captured still return a row (None counters) via the tolerant walk.
    """
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.NvLinkPorts, "nvlink-ports")
    assert isinstance(result.data, list) and result.data
    # only NVLink ports on GPU processors are returned
    assert all(r["GPU"].startswith("GPU_") for r in result.data)
    assert all(r["Port"].startswith("NVLink") for r in result.data)
    # GPU_0 is fully captured: a port there carries real traffic counters
    gpu0 = [r for r in result.data if r["GPU"] == "GPU_0" and r["RXBytes"] is not None]
    assert gpu0, "expected captured RX/TX counters on GPU_0 NVLink ports"
    row = gpu0[0]
    assert row["LinkStatus"] in ("LinkUp", "LinkDown", "NoLink", None)
    assert isinstance(row["RXBytes"], int)
