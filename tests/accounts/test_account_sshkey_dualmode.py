"""Dual-mode tests for the HPE account SSH-key import command."""

import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

RSA_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ0abcdef user@host"
ACCOUNT_URI = "/redfish/v1/AccountService/Accounts/42"
ACCOUNT_COLLECTION_URI = "/redfish/v1/AccountService/Accounts"


def _patch_requests(redfish_service):
    """Return PATCH requests recorded by the mock Redfish service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "PATCH"
    ]


def _mutating_requests(redfish_service):
    """Return non-GET requests recorded by the mock Redfish service."""
    return [
        request
        for request in redfish_service.requests
        if request.method in {"PATCH", "POST", "DELETE"}
    ]


def _seed_hpe_account(redfish_service):
    """Seed the HPE account collection because the HPE corpus lacks Accounts."""
    redfish_service._overlay[ACCOUNT_COLLECTION_URI.lower()] = {
        "@odata.id": ACCOUNT_COLLECTION_URI,
        "Members@odata.count": 1,
        "Members": [{"@odata.id": ACCOUNT_URI}],
    }
    redfish_service._overlay[ACCOUNT_URI.lower()] = {
        "@odata.id": ACCOUNT_URI,
        "Id": "42",
        "UserName": "sshuser",
        "Oem": {"Hpe": {"SSHKeys": []}},
    }


def test_account_import_sshkey_dry_run_reports_hpe_payload_without_patch(
    redfish_mock_factory,
):
    """account-import-sshkey dry-run resolves HPE account data but writes nothing."""
    manager, service = redfish_mock_factory("hpe")
    _seed_hpe_account(service)

    result = manager.sync_invoke(
        ApiRequestType.AccountImportSSHKey,
        "account-import-sshkey",
        acct_user="sshuser",
        ssh_key=RSA_KEY,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "import-ssh-key"
    assert result.data["target"] == "sshuser"
    assert result.data["uri"] == ACCOUNT_URI
    assert result.data["key_preview"] == f"{RSA_KEY[:40]}..."
    json.dumps(result.data)
    assert [request.method for request in service.requests] == ["GET", "GET"]
    assert _mutating_requests(service) == []


def test_account_import_sshkey_confirm_patches_hpe_sshkeys_payload(
    redfish_mock_factory,
):
    """account-import-sshkey --confirm PATCHes the documented HPE SSHKeys body."""
    manager, service = redfish_mock_factory("hpe")
    _seed_hpe_account(service)

    result = manager.sync_invoke(
        ApiRequestType.AccountImportSSHKey,
        "account-import-sshkey",
        acct_user="sshuser",
        ssh_key=RSA_KEY,
        acct_confirm=True,
    )

    patch_requests = _patch_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "import-ssh-key"
    assert result.data["target"] == "sshuser"
    assert result.data["status"] == "RedfishApiRespond.Ok"
    assert [request.method for request in service.requests] == ["GET", "GET", "PATCH"]
    assert len(patch_requests) == 1
    assert patch_requests[0].path.lower() == ACCOUNT_URI.lower()
    assert patch_requests[0].json() == {
        "Oem": {"Hpe": {"SSHKeys": [RSA_KEY]}},
    }


def test_account_import_sshkey_remove_patches_empty_hpe_sshkeys(
    redfish_mock_factory,
):
    """account-import-sshkey --remove --confirm clears the HPE SSHKeys list."""
    manager, service = redfish_mock_factory("hpe")
    _seed_hpe_account(service)

    result = manager.sync_invoke(
        ApiRequestType.AccountImportSSHKey,
        "account-import-sshkey",
        acct_user="sshuser",
        ssh_remove=True,
        acct_confirm=True,
    )

    patch_requests = _patch_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "remove-ssh-key"
    assert result.data["target"] == "sshuser"
    assert [request.method for request in service.requests] == ["GET", "GET", "PATCH"]
    assert len(patch_requests) == 1
    assert patch_requests[0].path.lower() == ACCOUNT_URI.lower()
    assert patch_requests[0].json() == {"Oem": {"Hpe": {"SSHKeys": []}}}


def test_account_import_sshkey_refuses_non_hpe_account_without_patch(
    redfish_mock_factory,
):
    """account-import-sshkey refuses non-HPE accounts before sending a PATCH."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.AccountImportSSHKey,
        "account-import-sshkey",
        acct_user="root",
        ssh_key=RSA_KEY,
        acct_confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data is None
    assert "HPE-only" in result.error
    assert [request.method for request in service.requests] == ["GET", "GET"]
    assert _mutating_requests(service) == []
