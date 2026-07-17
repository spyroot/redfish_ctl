"""Dual-mode-style coverage for TelemetryService.ClearMetricReports."""

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType

_TELEMETRY_SERVICE = "/redfish/v1/TelemetryService"
_CLEAR_TARGET = (
    "/redfish/v1/TelemetryService/Actions/TelemetryService.ClearMetricReports"
)


def _post_requests(service):
    return [request for request in service.requests if request.method == "POST"]


def _seed_clear_metric_reports(service):
    body = dict(service._state(_TELEMETRY_SERVICE))
    body["Actions"] = {
        "#TelemetryService.ClearMetricReports": {
            "target": _CLEAR_TARGET,
        },
    }
    service._overlay[_TELEMETRY_SERVICE] = body
    service._overlay[_TELEMETRY_SERVICE.lower()] = body


def test_telemetry_clear_reports_dry_run_blocks_post(redfish_mock_factory):
    """The clear action previews by default and does not POST."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_clear_metric_reports(service)

    result = manager.sync_invoke(
        ApiRequestType.TelemetryClearReports,
        "telemetry-clear-reports",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#TelemetryService.ClearMetricReports",
        "target": _CLEAR_TARGET,
        "payload": {},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(service) == []


def test_telemetry_clear_reports_confirm_posts_to_discovered_target(redfish_mock_factory):
    """With --confirm the command POSTs to the target from the Actions block."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_clear_metric_reports(service)

    result = manager.sync_invoke(
        ApiRequestType.TelemetryClearReports,
        "telemetry-clear-reports",
        confirm=True,
    )

    posts = _post_requests(service)
    assert len(posts) == 1
    assert posts[0].path == _CLEAR_TARGET.lower()
    assert posts[0].json() == {}
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#TelemetryService.ClearMetricReports"
    assert result.data["target"] == _CLEAR_TARGET
    assert result.data["level"] == "destructive"


def test_telemetry_clear_reports_missing_action_reports_available(redfish_mock_factory):
    """A BMC without ClearMetricReports returns a structured missing-action error."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.TelemetryClearReports,
        "telemetry-clear-reports",
        confirm=True,
    )

    assert result.error == (
        "action '#TelemetryService.ClearMetricReports' not found on "
        "/redfish/v1/TelemetryService"
    )
    assert result.data["action"] == "#TelemetryService.ClearMetricReports"
    assert result.data["available"] == []
    assert _post_requests(service) == []
