"""Dual-mode test for the accounts query command."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_accounts_query_returns_account_collection(redfish_api):
    """query_accounts returns the iDRAC account collection."""
    result = redfish_api.sync_invoke(ApiRequestType.QueryAccounts, "query_accounts")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/AccountService/Accounts"
    assert result.data["Members"][0]["@odata.id"] == (
        "/redfish/v1/AccountService/Accounts/2"
    )


def test_accounts_query_expanded_saves_collection_to_file(redfish_api, tmp_path):
    """query_accounts with expansion saves the collection response."""
    output = tmp_path / "accounts-expanded"

    result = redfish_api.sync_invoke(
        ApiRequestType.QueryAccounts,
        "query_accounts",
        do_expanded=True,
        filename=str(output),
    )

    saved_path = output.with_suffix(".json")
    assert saved_path.exists()
    saved = json.loads(saved_path.read_text())

    assert isinstance(result, CommandResult)
    assert saved == result.data
    assert saved["@odata.id"].endswith("/AccountService/Accounts")
    assert isinstance(saved["Members"], list)
    assert saved["Members@odata.count"] == len(saved["Members"])


def test_accounts_query_expanded_uses_expand_query_in_mock_mode(
    redfish_mock, redfish_service
):
    """query_accounts with expansion requests the Redfish expand query."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.QueryAccounts,
        "query_accounts",
        do_expanded=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["Members"][0]["@odata.id"] == (
        "/redfish/v1/AccountService/Accounts/2"
    )
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/AccountService/Accounts".lower()
    )
    assert "expand" in redfish_service.last_request.url.lower()
