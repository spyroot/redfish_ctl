"""Dual-mode test for the metric-definitions command."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_metric_definitions_dualmode_returns_json_list_without_post(request, monkeypatch):
    """metric-definitions returns a JSON list in offline dual-mode mock transport."""
    monkeypatch.delenv("IDRAC_IP", raising=False)
    redfish_api = request.getfixturevalue("redfish_api")
    redfish_service = request.getfixturevalue("redfish_service")

    result = redfish_api.sync_invoke(
        ApiRequestType.MetricReportDefinitions,
        "metric-definitions",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert redfish_service.requests
    assert all(recorded.method != "POST" for recorded in redfish_service.requests)
