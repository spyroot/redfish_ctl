"""Import or remove an authorized SSH public key for an account (HPE iLO OEM).

    idrac_ctl account-import-sshkey --username test --key-file ~/.ssh/id_rsa.pub --confirm
    idrac_ctl account-import-sshkey --username test --remove --confirm

HPE iLO 5 stores per-account authorized SSH keys under ``Oem.Hpe.SSHKeys`` (a list
of OpenSSH key strings), set by PATCHing the account resource. This is HPE-specific
OEM — other vendors expose SSH keys differently (Dell/Supermicro not mapped yet),
so the command refuses on a non-HPE account rather than sending a payload that
would not apply.

HPE constraints enforced here: the public key must be RSA (DSA is blocked on iLO
firmware >= 2.10 in Production mode) and at most 1366 bytes. Deleting the account
auto-purges its keys, so a leftover test account never leaves a dangling key.

NOTE — acceptance is firmware-dependent. This sends HPE's *documented* body, but a
live iLO 5 on fw 2.96 rejects it with ``PropertyNotWritableOrUnknown`` (its Web UI
uses a different, undocumented path there). The offline test asserts the request
shape against the stateful mock; confirm acceptance against your own firmware.

Author Mus spyroot@gmail.com
"""
import os
from abc import abstractmethod
from typing import Optional

from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from .cmd_account_manage import _AccountBase

# HPE iLO's documented authorized-SSH-key size limit.
MAX_SSH_KEY_BYTES = 1366


def validate_ssh_key(key: str) -> Optional[str]:
    """Return an error string if ``key`` is not an iLO-acceptable SSH pubkey, else None."""
    key = (key or "").strip()
    if not key:
        return "empty SSH key"
    kind = key.split(None, 1)[0]
    if not (kind.startswith("ssh-") or kind.startswith("ecdsa-")):
        return "not an OpenSSH public key (expected 'ssh-rsa AAAA...')"
    if kind == "ssh-dss":
        return "DSA keys are blocked by iLO (fw >= 2.10, Production mode) — use an RSA key"
    n = len(key.encode("utf-8"))
    if n > MAX_SSH_KEY_BYTES:
        return f"key is {n} bytes, exceeds the iLO limit of {MAX_SSH_KEY_BYTES}"
    return None


class AccountImportSSHKey(_AccountBase,
                         scm_type=ApiRequestType.AccountImportSSHKey,
                         name='account-import-sshkey',
                         metaclass=Singleton):
    """PATCH an account's Oem.Hpe.SSHKeys to authorize (or clear) an SSH key."""

    def __init__(self, *args, **kwargs):
        super(AccountImportSSHKey, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--username', required=False, type=str, dest='acct_user', default=None)
        cmd_parser.add_argument('--id', required=False, type=str, dest='acct_id', default=None)
        cmd_parser.add_argument('--key', required=False, type=str, dest='ssh_key', default=None,
                                help="the SSH public key string (ssh-rsa AAAA...)")
        cmd_parser.add_argument('--key-file', required=False, type=str, dest='ssh_key_file', default=None,
                                help="path to a .pub file to read the key from (e.g. ~/.ssh/id_rsa.pub)")
        cmd_parser.add_argument('--remove', action='store_true', required=False, dest='ssh_remove',
                                default=False, help="clear the account's authorized SSH keys")
        cmd_parser.add_argument('--confirm', action='store_true', required=False,
                                dest='acct_confirm', default=False,
                                help="actually write (otherwise dry-run)")
        return cmd_parser, "account-import-sshkey", "authorize/clear an account SSH key (HPE OEM, guarded)"

    def execute(self, acct_user=None, acct_id=None, ssh_key=None, ssh_key_file=None,
                ssh_remove=False, acct_confirm=False, **kwargs) -> CommandResult:
        if not acct_user and not acct_id:
            return CommandResult(None, None, None, "provide --username or --id")

        if ssh_remove:
            keys = []
            key = None
        else:
            key = ssh_key
            if key is None and ssh_key_file:
                path = os.path.expanduser(ssh_key_file)
                try:
                    with open(path, "r") as fh:
                        key = fh.read()
                except OSError as e:
                    return CommandResult(None, None, None, f"cannot read key file {path}: {e}")
            if not key:
                return CommandResult(None, None, None, "provide --key, --key-file, or --remove")
            err = validate_ssh_key(key)
            if err:
                return CommandResult(None, None, None, f"invalid SSH key: {err}")
            keys = [key.strip()]

        uri, data = self._resolve_account(acct_user, acct_id)
        if not uri:
            return CommandResult(None, None, None, f"account not found: {acct_user or acct_id}")
        if "Hpe" not in (data.get("Oem") or {}):
            vendor = list((data.get("Oem") or {}).keys()) or ["unknown"]
            return CommandResult(
                None, None, None,
                f"SSH key import is HPE-only for now (Oem.Hpe.SSHKeys); this account's OEM is {vendor}")

        payload = {"Oem": {"Hpe": {"SSHKeys": keys}}}
        action = "remove-ssh-key" if ssh_remove else "import-ssh-key"
        if not acct_confirm:
            preview = None if ssh_remove else (keys[0][:40] + "...")
            return CommandResult(
                {"dry_run": True, "action": action, "target": data.get("UserName"),
                 "uri": uri, "key_preview": preview,
                 "hint": "re-run with --confirm to write"}, None, None, None)
        res, status = self.base_patch(uri, payload=payload)
        return CommandResult(
            {"action": action, "target": data.get("UserName"), "status": str(status),
             "error": res.error}, None, None, res.error)
