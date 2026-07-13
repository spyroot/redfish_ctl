"""Dual-mode-style tests for the generic logs command."""
import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_logs_reads_limited_hpe_log_entries_without_mutation(
    redfish_mock_factory,
):
    """logs walks HPE system/manager LogServices and caps entries per service."""
    mgr, service = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(ApiRequestType.Logs, "logs", limit=1)

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data)
    assert result.data == [
        {
            "Source": "1",
            "Service": "IML",
            "Id": "1",
            "Severity": "OK",
            "Created": "2025-04-04T18:17:40Z",
            "Message": "IML Cleared ( user: System Administrator)",
        },
        {
            "Source": "1",
            "Service": "SL",
            "Id": "1",
            "Severity": "OK",
            "Created": "2025-04-04T17:10:23Z",
            "Message": "Security log cleared by: Administrator",
        },
        {
            "Source": "1",
            "Service": "Event",
            "Id": "1",
            "Severity": "OK",
            "Created": "0000-00-00T00:00:00Z",
            "Message": None,
        },
        {
            "Source": "1",
            "Service": "IEL",
            "Id": "1",
            "Severity": "OK",
            "Created": "2025-06-03T10:50:08Z",
            "Message": (
                "The iLO health monitoring status of the device / adapter located "
                "in Slot 14 has OS driver in persistent mode. Fixed reading "
                "thermal limits is not responsive."
            ),
        },
    ]
    assert {
        request.method
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
