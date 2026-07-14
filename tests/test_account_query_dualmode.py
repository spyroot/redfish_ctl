"""Dual-mode tests for the individual account query command."""
import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_account_query_returns_manager_account(redfish_api):
    """query_account returns the requested ManagerAccount resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryAccount,
        "query_account",
        account="2",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/AccountService/Accounts/2"
    assert result.data["Id"] == "2"
    assert result.data["UserName"]
