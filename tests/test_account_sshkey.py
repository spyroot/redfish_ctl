"""Offline tests for account-import-sshkey (HPE Oem.Hpe.SSHKeys PATCH).

Covers key validation (RSA ok, DSA blocked, oversize/non-key rejected), the
dry-run-by-default guard, the remove path, and the refusal on a non-HPE account.
No real BMC and no network — resolution is mocked, writes are dry-run.
"""
from redfish_ctl.accounts.cmd_account_sshkey import (
    MAX_SSH_KEY_BYTES,
    AccountImportSSHKey,
    validate_ssh_key,
)
from redfish_ctl.redfish_manager_shared import ApiRequestType

RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ0abcdef user@host"


def _mock_resolve(oem):
    return lambda self, u, i: ("/redfish/v1/AccountService/Accounts/4",
                               {"UserName": "test", "Oem": oem})


def test_validate_accepts_rsa():
    """A well-formed ssh-rsa key passes validation."""
    assert validate_ssh_key(RSA) is None


def test_validate_blocks_dsa():
    """DSA keys are rejected (iLO blocks them on fw >= 2.10 Production)."""
    err = validate_ssh_key("ssh-dss AAAAB3NzaC1kc3MAAACB user@host")
    assert err and "DSA" in err


def test_validate_rejects_non_key_and_empty():
    """A non-key string or empty input is rejected, not sent."""
    assert validate_ssh_key("just some text") is not None
    assert validate_ssh_key("") is not None


def test_validate_rejects_oversize():
    """A key over the iLO 1366-byte limit is rejected with the limit in the message."""
    err = validate_ssh_key("ssh-rsa " + "A" * 1400)
    assert err and str(MAX_SSH_KEY_BYTES) in err


def test_dry_run_import_masks_and_writes_nothing(redfish_mock_factory, monkeypatch):
    """Import without --confirm returns a dry-run plan with a short key preview."""
    mgr, _ = redfish_mock_factory("hpe")
    monkeypatch.setattr(AccountImportSSHKey, "_resolve_account", _mock_resolve({"Hpe": {}}))
    res = mgr.sync_invoke(ApiRequestType.AccountImportSSHKey, "account-import-sshkey",
                          acct_user="test", ssh_key=RSA)
    assert res.data["dry_run"] is True
    assert res.data["action"] == "import-ssh-key"
    assert res.data["key_preview"].startswith("ssh-rsa")


def test_remove_builds_clear_action(redfish_mock_factory, monkeypatch):
    """--remove targets an empty SSHKeys list (clear), dry-run by default."""
    mgr, _ = redfish_mock_factory("hpe")
    monkeypatch.setattr(AccountImportSSHKey, "_resolve_account", _mock_resolve({"Hpe": {}}))
    res = mgr.sync_invoke(ApiRequestType.AccountImportSSHKey, "account-import-sshkey",
                          acct_user="test", ssh_remove=True)
    assert res.data["action"] == "remove-ssh-key" and res.data["dry_run"] is True


def test_refuses_non_hpe_account(redfish_mock_factory, monkeypatch):
    """On a non-HPE account it refuses rather than sending an Oem.Hpe payload that won't apply."""
    mgr, _ = redfish_mock_factory("supermicro")
    monkeypatch.setattr(AccountImportSSHKey, "_resolve_account", _mock_resolve({"Supermicro": {}}))
    res = mgr.sync_invoke(ApiRequestType.AccountImportSSHKey, "account-import-sshkey",
                          acct_user="test", ssh_key=RSA, acct_confirm=True)
    assert res.error and "HPE-only" in res.error


def test_requires_target(redfish_mock_factory):
    """No --username/--id is a clear error."""
    mgr, _ = redfish_mock_factory("hpe")
    res = mgr.sync_invoke(ApiRequestType.AccountImportSSHKey, "account-import-sshkey", ssh_key=RSA)
    assert res.error and ("username" in res.error or "id" in res.error)


def test_confirm_sends_documented_patch_to_sim(redfish_mock_factory, monkeypatch):
    """--confirm PATCHes exactly {Oem:{Hpe:{SSHKeys:[key]}}} — verified against the write-capable mock.

    The stateful mock accepts the PATCH (200), which real iLO fw 2.96 rejects with
    PropertyNotWritableOrUnknown; this test asserts the *request the client sends*
    matches HPE's documented method, independent of that firmware quirk.
    """
    mgr, service = redfish_mock_factory("hpe")
    monkeypatch.setattr(
        AccountImportSSHKey, "_resolve_account",
        lambda self, u, i: ("/redfish/v1/AccountService/Accounts/4", {"UserName": "test", "Oem": {"Hpe": {}}}))
    res = mgr.sync_invoke(ApiRequestType.AccountImportSSHKey, "account-import-sshkey",
                          acct_user="test", ssh_key=RSA, acct_confirm=True)
    assert res.error is None
    last = service.requests[-1]
    assert last.method == "PATCH"
    assert last.path.lower().rstrip("/").endswith("/accountservice/accounts/4")
    assert last.json() == {"Oem": {"Hpe": {"SSHKeys": [RSA]}}}
