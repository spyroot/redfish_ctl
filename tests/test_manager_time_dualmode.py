"""Dual-mode coverage for the manager-time command."""

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_manager_time_read_reports_manager_datetime(redfish_api):
    """manager-time reads manager DateTime rows without writing."""
    result = redfish_api.sync_invoke(ApiRequestType.ManagerTime, "manager-time")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert isinstance(result.data, list)
    assert result.data
    for row in result.data:
        assert row["Manager"]
        assert "DateTime" in row
        assert "DateTimeLocalOffset" in row
        assert "WriteStatus" not in row
        assert "WriteError" not in row


def test_manager_time_set_datetime_patches_manager_in_mock_mode(
    redfish_mock, redfish_service
):
    """manager-time with --set sends the requested Manager DateTime PATCH."""
    requested = "2026-07-02T20:00:00+00:00"

    result = redfish_mock.sync_invoke(
        ApiRequestType.ManagerTime,
        "manager-time",
        set_datetime=requested,
        set_offset="+00:00",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data[0]["Requested"] == requested
    assert result.data[0]["DateTime"] == requested
    assert result.data[0]["DateTimeLocalOffset"] == "+00:00"
    assert result.data[0]["WriteError"] is None

    patch_requests = [
        request
        for request in redfish_service.requests
        if request.method == "PATCH"
    ]
    assert len(patch_requests) == 1
    request = patch_requests[0]
    assert request.path.lower() == "/redfish/v1/managers/idrac.embedded.1"
    assert request.json() == {
        "DateTime": requested,
        "DateTimeLocalOffset": "+00:00",
    }
