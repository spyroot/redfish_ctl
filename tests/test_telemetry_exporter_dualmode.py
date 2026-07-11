"""Dual-mode smoke tests for the telemetry exporter command."""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_exporter_once_returns_prometheus_metrics_from_mock_reads(
    redfish_api,
    redfish_service,
):
    """exporter --once renders Prometheus metrics using read-only Redfish GETs."""
    result = redfish_api.sync_invoke(
        ApiRequestType.Exporter,
        "exporter",
        once=True,
        exporter_output="prometheus",
        vendor="dell",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.extra == {"sample_count": 5}
    assert isinstance(result.data, str)
    assert "hw.temperature" in result.data
    assert "hw.scrape.ok" in result.data
    assert "hw.scrape.duration_seconds" in result.data
    assert 'vendor="dell"' in result.data
    assert redfish_service.requests
    assert {request.method for request in redfish_service.requests} == {"GET"}
