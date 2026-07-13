"""Dual-mode tests for the ComponentIntegrity query command."""
import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_component_integrity_absent_collection_returns_empty_list(
    redfish_mock, redfish_service
):
    """component-integrity tolerates a host with no attestation collection."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.ComponentIntegrity,
        "component-integrity",
    )

    assert isinstance(result, CommandResult)
    json.dumps(result.data)
    assert result.data == []
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None

    assert len(redfish_service.requests) == 1
    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower() == "/redfish/v1/componentintegrity".lower()
