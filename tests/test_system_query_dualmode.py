"""Dual-mode test for the system query command."""
import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_system_query_returns_system_resource(redfish_api):
    """system_query returns the iDRAC ComputerSystem resource."""
    result = redfish_api.sync_invoke(ApiRequestType.SystemQuery, "system_query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1"
    assert result.data["Id"] == "System.Embedded.1"
