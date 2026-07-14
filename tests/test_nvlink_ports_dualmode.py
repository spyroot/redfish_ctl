"""Dual-mode-style mock test for the NVLink ports command."""
import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_nvlink_ports_returns_empty_list_without_gpu_links(
    redfish_mock, redfish_service
):
    """nvlink-ports returns no rows when the mock tree has no GPU NVLink links."""
    result = redfish_mock.sync_invoke(ApiRequestType.NvLinkPorts, "nvlink-ports")
    post_requests = [
        request for request in redfish_service.requests
        if request.method == "POST"
    ]

    assert isinstance(result, CommandResult)
    json.dumps(result.data)
    assert result.data == []
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert post_requests == []
