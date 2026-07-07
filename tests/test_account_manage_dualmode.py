"""Dual-mode tests for guarded account management commands."""

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult

ACCOUNTS_PATH = "/redfish/v1/AccountService/Accounts"
ACCOUNT_THREE_PATH = f"{ACCOUNTS_PATH}/3"


def _seed_operator_account(redfish_service):
    """Add a non-self account to the in-memory mock AccountService collection."""
    redfish_service._overlay[ACCOUNTS_PATH.lower()] = {
        "@odata.id": ACCOUNTS_PATH,
        "Members": [
            {"@odata.id": f"{ACCOUNTS_PATH}/2"},
            {"@odata.id": ACCOUNT_THREE_PATH},
        ],
        "Members@odata.count": 2,
    }
    redfish_service._overlay[ACCOUNT_THREE_PATH.lower()] = {
        "@odata.id": ACCOUNT_THREE_PATH,
        "Id": "3",
        "UserName": "operator",
        "RoleId": "Operator",
        "Enabled": True,
    }


def test_account_create_dry_run_masks_password_without_post(
    redfish_mock,
    redfish_service,
):
    """account-create stays dry-run by default and never sends the password."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.AccountCreate,
        "account-create",
        acct_user="operator",
        acct_password="placeholder-password",
        acct_role="Operator",
        acct_confirm=False,
    )

    assert isinstance(result, CommandResult)
    assert result.data["dry_run"] is True
    assert result.data["action"] == "create"
    assert result.data["payload"] == {
        "UserName": "operator",
        "Password": "***",
        "RoleId": "Operator",
    }
    assert result.error is None
    assert not redfish_service.requests


def test_account_update_confirm_patches_resolved_account(
    redfish_mock,
    redfish_service,
):
    """account-update resolves by UserName and PATCHes only requested fields."""
    _seed_operator_account(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.AccountUpdate,
        "account-update",
        acct_user="operator",
        acct_role="ReadOnly",
        acct_enabled="false",
        acct_confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "update"
    assert result.data["target"] == "operator"

    patch_requests = [
        request for request in redfish_service.requests if request.method == "PATCH"
    ]
    assert len(patch_requests) == 1
    assert patch_requests[0].path.lower() == ACCOUNT_THREE_PATH.lower()
    assert patch_requests[0].json() == {"RoleId": "ReadOnly", "Enabled": False}


def test_account_delete_dry_run_resolves_target_without_delete(
    redfish_mock,
    redfish_service,
):
    """account-delete previews the resolved target until --confirm is supplied."""
    _seed_operator_account(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.AccountDelete,
        "account-delete",
        acct_user="operator",
        acct_confirm=False,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "delete"
    assert result.data["target"] == "operator"
    assert result.data["uri"] == ACCOUNT_THREE_PATH
    assert all(request.method != "DELETE" for request in redfish_service.requests)
