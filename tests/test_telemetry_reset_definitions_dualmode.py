"""Dual-mode-style coverage for resetting TelemetryService report definitions."""

import json

import pytest

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import ApiRequestType

TELEMETRY_SERVICE = "/redfish/v1/TelemetryService"
RESET_ACTION = "#TelemetryService.ResetMetricReportDefinitionsToDefaults"
RESET_TARGET = (
    "/redfish/v1/TelemetryService/Actions/"
    "TelemetryService.ResetMetricReportDefinitionsToDefaults"
)


@pytest.fixture
def telemetry_reset_manager(redfish_mock_factory):
    """Serve Supermicro fixtures with the reset action added to TelemetryService."""
    manager, service = redfish_mock_factory("supermicro")
    service_body = service._state(TELEMETRY_SERVICE)
    service_with_action = {
        **service_body,
        "Actions": {
            RESET_ACTION: {
                "target": RESET_TARGET,
            },
        },
    }
    service._overlay[TELEMETRY_SERVICE] = service_with_action
    service._overlay[TELEMETRY_SERVICE.lower()] = service_with_action
    return manager, service


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in service.requests if request.method == "POST"]


def test_telemetry_reset_definitions_without_confirm_is_preview_only(
        telemetry_reset_manager):
    """The reset action resolves but does not POST without --confirm."""
    manager, service = telemetry_reset_manager

    result = manager.sync_invoke(
        ApiRequestType.TelemetryResetMetricDefinitions,
        "telemetry-reset-definitions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == RESET_ACTION
    assert result.data["target"] == RESET_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_telemetry_reset_definitions_confirm_posts_empty_payload(
        telemetry_reset_manager):
    """--confirm POSTs the reset action to the discovered target."""
    manager, service = telemetry_reset_manager

    result = manager.sync_invoke(
        ApiRequestType.TelemetryResetMetricDefinitions,
        "telemetry-reset-definitions",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == RESET_ACTION
    assert result.data["target"] == RESET_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == RESET_TARGET.lower()
    assert posts[0].json() == {}


def test_telemetry_reset_definitions_confirm_dry_run_still_does_not_post(
        telemetry_reset_manager):
    """--dry_run remains a no-POST preview even when --confirm is present."""
    manager, service = telemetry_reset_manager

    result = manager.sync_invoke(
        ApiRequestType.TelemetryResetMetricDefinitions,
        "telemetry-reset-definitions",
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == RESET_TARGET
    assert _post_requests(service) == []


def test_telemetry_reset_definitions_missing_action_reports_error(
        redfish_mock_factory):
    """A TelemetryService without the reset action errors without POSTing."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.TelemetryResetMetricDefinitions,
        "telemetry-reset-definitions",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#TelemetryService.ResetMetricReportDefinitionsToDefaults' "
        "not found on /redfish/v1/TelemetryService"
    )
    assert result.data["action"] == RESET_ACTION
    assert RESET_ACTION not in result.data["available"]
    assert _post_requests(service) == []


def test_telemetry_reset_definitions_result_is_json_serializable(
        telemetry_reset_manager):
    """The preview payload can be rendered through the normal JSON output path."""
    manager, _service = telemetry_reset_manager

    result = manager.sync_invoke(
        ApiRequestType.TelemetryResetMetricDefinitions,
        "telemetry-reset-definitions",
        dry_run=True,
    )

    json.dumps(result.data, sort_keys=True)
