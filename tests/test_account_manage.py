"""Offline tests for account create/update/delete (ManagerAccount writes).

Covers the pure payload builders, the dry-run-by-default guard (no write, and the
password is never echoed), the "nothing to change" / "no target" errors, and the
self-delete guard that refuses to remove the logged-in account. No real BMC and
no network — writes are mocked or dry-run.
"""
import pytest

from redfish_ctl.accounts.cmd_account_manage import (
    DEFAULT_ROLE,
    AccountDelete,
    _mask,
    build_create_payload,
    build_update_payload,
)
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_build_create_payload():
    """Create body carries UserName, Password, and the requested RoleId."""
    assert build_create_payload("test", "pw", "Operator") == {
        "UserName": "test", "Password": "pw", "RoleId": "Operator"}


@pytest.mark.parametrize("role", ["Administrator", "Operator", "ReadOnly"])
def test_create_payload_covers_all_three_roles(role):
    """All three standard privilege levels build a correct create body.

    These are the RoleIds every vendor exposes (Dell/HPE/Supermicro) and were
    each verified end-to-end on a live HPE iLO 5 (create -> RoleId read-back ->
    delete).
    """
    assert build_create_payload("test", "pw", role) == {
        "UserName": "test", "Password": "pw", "RoleId": role}


def test_build_create_payload_defaults_to_readonly():
    """No role given → least-privilege ReadOnly, not a blank/None role."""
    assert build_create_payload("test", "pw", None)["RoleId"] == DEFAULT_ROLE == "ReadOnly"


def test_build_update_payload_only_set_fields():
    """Update body contains only the fields the caller actually set."""
    assert build_update_payload(None, "Operator", None) == {"RoleId": "Operator"}
    assert build_update_payload("pw", None, True) == {"Password": "pw", "Enabled": True}
    assert build_update_payload(None, None, None) == {}


def test_mask_hides_password():
    """Password is masked for display; other fields pass through."""
    masked = _mask({"UserName": "t", "Password": "secret", "RoleId": "ReadOnly"})
    assert masked["Password"] == "***" and masked["UserName"] == "t"


def test_create_dry_run_writes_nothing_and_masks_password(redfish_mock_factory):
    """account-create without --confirm returns a dry-run plan, never the real pw."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.AccountCreate, "account-create",
                          acct_user="test", acct_password="Secret123!",
                          acct_role="ReadOnly", acct_confirm=False)
    assert res.data["dry_run"] is True
    assert res.data["payload"]["UserName"] == "test"
    assert res.data["payload"]["Password"] == "***"   # real password never surfaced


def test_update_requires_a_field(redfish_mock_factory):
    """account-update with no --role/--password/--enabled is a clear no-op error."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.AccountUpdate, "account-update", acct_user="test")
    assert res.error and "nothing to update" in res.error


def test_delete_requires_target(redfish_mock_factory):
    """account-delete with no target refuses rather than guessing."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.AccountDelete, "account-delete")
    assert res.error and ("username" in res.error or "id" in res.error)


def test_delete_self_guard(redfish_mock_factory, monkeypatch):
    """Deleting the logged-in account is refused even with --confirm (no lockout)."""
    mgr, _ = redfish_mock_factory("supermicro")
    me = mgr._username
    monkeypatch.setattr(
        AccountDelete, "_resolve_account",
        lambda self, u, i: ("/redfish/v1/AccountService/Accounts/1", {"UserName": me, "Id": "1"}))
    res = mgr.sync_invoke(ApiRequestType.AccountDelete, "account-delete",
                          acct_user=me, acct_confirm=True)
    assert res.error and "self-delete" in res.error
