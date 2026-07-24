"""Offline test for the generic, vendor-neutral `metric-reports` command.

Runs against the Supermicro GB300 fixtures (TelemetryService -> MetricReports ->
HGX_ProcessorMetrics_0) through the real requests path, proving the command
navigates by links and flattens MetricValues on a non-Dell host. This is the
out-of-band GPU/accelerator telemetry path (no host OS, no driver).
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_metric_reports_reads_telemetry(redfish_mock_factory):
    """metric-reports walks TelemetryService and flattens each MetricValue.

    The real GB300 capture exposes 10 MetricReports (platform + CPU/GPU/memory),
    so the command must surface every report, not just one.
    """
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.MetricReports, "metric-reports")
    assert isinstance(result.data, list) and result.data, "no metric values"
    reports = {row["Report"] for row in result.data}
    # the GPU processor report and the platform-environment report are both present
    assert "HGX_ProcessorMetrics_0" in reports
    assert "PlatformEnvironmentMetrics_0" in reports
    assert len(reports) == 10
    row = result.data[0]
    assert row["MetricProperty"], "sample must carry its MetricProperty key"
    # MetricValue is a Redfish string, preserved verbatim (not cast/dropped).
    assert isinstance(row["MetricValue"], str)


def test_metric_reports_report_filter(redfish_mock_factory):
    """--report substring narrows to matching report ids; a miss yields nothing."""
    mgr, _ = redfish_mock_factory("supermicro")
    # an exact id matches only its own report (six reports merely contain
    # "Processor", so the filter must be specific to scope to the GPU one).
    hit = mgr.sync_invoke(ApiRequestType.MetricReports, "metric-reports",
                          report="HGX_ProcessorMetrics_0")
    assert hit.data and all(r["Report"] == "HGX_ProcessorMetrics_0" for r in hit.data)

    miss = mgr.sync_invoke(ApiRequestType.MetricReports, "metric-reports",
                           report="NoSuchReport")
    assert miss.data == []


def test_metric_reports_no_telemetry_service(redfish_mock):
    """A host without TelemetryService returns an empty list, not an error.

    The default Dell-shaped mock has no TelemetryService fixture, so the initial
    collection GET fails and the command degrades to an empty result.
    """
    result = redfish_mock.sync_invoke(ApiRequestType.MetricReports, "metric-reports")
    assert result.data == []
