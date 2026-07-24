"""Dual-mode smoke tests for the read-only metric-reports command."""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_metric_reports_dualmode_returns_json_list_without_post(request, monkeypatch):
    """metric-reports returns a JSON list in offline dual-mode mock transport."""
    monkeypatch.delenv("IDRAC_IP", raising=False)
    redfish_api = request.getfixturevalue("redfish_api")
    redfish_service = request.getfixturevalue("redfish_service")

    result = redfish_api.sync_invoke(ApiRequestType.MetricReports, "metric-reports")

    assert isinstance(result, CommandResult)
    assert result.data == []
    json.dumps(result.data, sort_keys=True)
    assert redfish_service.requests
    assert all(recorded.method != "POST" for recorded in redfish_service.requests)
