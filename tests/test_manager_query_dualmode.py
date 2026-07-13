"""Dual-mode test for the manager query command."""
import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_manager_query_returns_base_manager(redfish_api):
    """manager_query follows the manager collection to the iDRAC manager."""
    result = redfish_api.sync_invoke(ApiRequestType.ManagerQuery, "manager_query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Managers/iDRAC.Embedded.1"
    assert result.data["Id"] == "iDRAC.Embedded.1"
