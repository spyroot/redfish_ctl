"""Create, update, and delete Redfish accounts (ManagerAccount).

    redfish_ctl account-create --username test --password '***' --role ReadOnly --confirm
    redfish_ctl account-update --username test --role Operator --confirm
    redfish_ctl account-delete --username test --confirm

Vendor-neutral: create POSTs to the AccountService ``Accounts`` collection,
update PATCHes ``Accounts/<id>``, delete DELETEs it — all standard Redfish that
works on Dell/HPE/Supermicro/generic. Every command is **dry-run by default**
(it prints the intended change, password masked, and writes nothing) and only
mutates with ``--confirm``. Delete additionally refuses to remove the account
you are logged in as, so a misfire can never lock you out.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional, Tuple

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult

# RoleId is the standard Redfish privilege model (present on Dell/HPE/Supermicro).
DEFAULT_ROLE = "ReadOnly"


def build_create_payload(username: str, password: str, role: str) -> dict:
    """Build the POST body that creates a ManagerAccount.

    :param username: UserName for the new account.
    :param password: cleartext password for the new account.
    :param role: Redfish RoleId to assign; falls back to ``DEFAULT_ROLE``
        (``ReadOnly``) when falsy (e.g. empty string or None).
    :return: dict payload with UserName/Password/RoleId keys.
    """
    return {"UserName": username, "Password": password, "RoleId": role or DEFAULT_ROLE}


def build_update_payload(password: Optional[str],
                         role: Optional[str],
                         enabled: Optional[bool]) -> dict:
    """Build a PATCH body from only the fields the caller actually set.

    :param password: new password, or None to leave it unchanged.
    :param role: new Redfish RoleId, or None to leave it unchanged.
    :param enabled: new Enabled state, or None to leave it unchanged.
    :return: dict with only the keys that were supplied (may be empty).
    """
    payload: dict = {}
    if password is not None:
        payload["Password"] = password
    if role is not None:
        payload["RoleId"] = role
    if enabled is not None:
        payload["Enabled"] = enabled
    return payload


def _mask(payload: dict) -> dict:
    """Return a copy of a payload with the Password value masked for display.

    :param payload: request body that may contain a ``Password`` key.
    :return: shallow copy with the ``Password`` value replaced by ``***``.
    """
    return {k: ("***" if k == "Password" else v) for k, v in payload.items()}


class _AccountBase(RedfishManagerBase):
    """Shared account resolution for the write commands."""

    def _resolve_account(self, username: Optional[str],
                         account_id: Optional[str]) -> Tuple[Optional[str], dict]:
        """Find an account by UserName or Id; return (uri, data) or (None, {}).

        Reads the Accounts collection and each member (vendor-neutral — it never
        assumes an id scheme), so it matches on the real ``UserName``/``Id``. The
        collection is fetched WITHOUT ``$expand`` because some services (HPE iLO 5)
        reject an expanded Accounts query with HTTP 400; each member is fetched
        individually instead.

        :param username: UserName to match, or None to match by id only.
        :param account_id: account Id to match, or None to match by username only.
        :return: tuple ``(uri, data)`` for the matched account, or
            ``(None, {})`` when no member matches.
        """
        coll = (self.base_query(REDFISH_API.Accounts).data or {})
        for m in coll.get("Members", []):
            data = m if "UserName" in m else (self.base_query(m.get("@odata.id", "")).data or {})
            uri = data.get("@odata.id") or m.get("@odata.id")
            if account_id and str(data.get("Id")) == str(account_id):
                return uri, data
            if username and data.get("UserName") == username:
                return uri, data
        return None, {}


class AccountCreate(_AccountBase,
                    scm_type=ApiRequestType.AccountCreate,
                    name='account-create',
                    metaclass=Singleton):
    """Create a new Redfish account (dry-run unless --confirm)."""

    def __init__(self, *args, **kwargs):
        """Initialize the account-create command."""
        super(AccountCreate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``account-create`` subcommand and its flags."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--username', required=True, type=str, dest='acct_user',
                                help="new account UserName")
        cmd_parser.add_argument('--password', required=True, type=str, dest='acct_password',
                                help="new account password (not logged)")
        cmd_parser.add_argument('--role', required=False, type=str, dest='acct_role',
                                default=DEFAULT_ROLE, help="RoleId (default ReadOnly)")
        cmd_parser.add_argument('--confirm', action='store_true', required=False,
                                dest='acct_confirm', default=False,
                                help="actually create (otherwise dry-run)")
        return cmd_parser, "account-create", "create a Redfish account (guarded)"

    def execute(self, acct_user=None, acct_password=None, acct_role=DEFAULT_ROLE,
                acct_confirm=False, **kwargs) -> CommandResult:
        """Create a Redfish account (dry-run unless confirmed).

        :param acct_user: UserName for the new account.
        :param acct_password: password for the new account (never logged).
        :param acct_role: Redfish RoleId to assign (default ``ReadOnly``).
        :param acct_confirm: when False (default) return the masked payload
            without writing; when True POST it to the Accounts collection.
        :return: CommandResult describing the dry-run preview or the create
            result (status and any error).
        """
        payload = build_create_payload(acct_user, acct_password, acct_role)
        if not acct_confirm:
            return CommandResult(
                {"dry_run": True, "action": "create", "payload": _mask(payload),
                 "hint": "re-run with --confirm to create"}, None, None, None)
        res, status = self.base_post(REDFISH_API.Accounts, payload=payload, expected_status=201)
        return CommandResult(
            {"action": "create", "username": acct_user, "role": acct_role,
             "status": str(status), "error": res.error}, None, None, res.error)


class AccountUpdate(_AccountBase,
                    scm_type=ApiRequestType.AccountUpdate,
                    name='account-update',
                    metaclass=Singleton):
    """Update a Redfish account's password/role/enabled (dry-run unless --confirm)."""

    def __init__(self, *args, **kwargs):
        """Initialize the account-update command."""
        super(AccountUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``account-update`` subcommand and its flags."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--username', required=False, type=str, dest='acct_user', default=None)
        cmd_parser.add_argument('--id', required=False, type=str, dest='acct_id', default=None,
                                help="target by account Id instead of username")
        cmd_parser.add_argument('--role', required=False, type=str, dest='acct_role', default=None)
        cmd_parser.add_argument('--password', required=False, type=str, dest='acct_password', default=None)
        cmd_parser.add_argument('--enabled', required=False, type=str, dest='acct_enabled', default=None,
                                help="true/false to enable or disable the account")
        cmd_parser.add_argument('--confirm', action='store_true', required=False,
                                dest='acct_confirm', default=False)
        return cmd_parser, "account-update", "update a Redfish account (guarded)"

    def execute(self, acct_user=None, acct_id=None, acct_role=None, acct_password=None,
                acct_enabled=None, acct_confirm=False, **kwargs) -> CommandResult:
        """Update a Redfish account's password, role, or enabled state.

        :param acct_user: UserName of the account to update (or use acct_id).
        :param acct_id: account Id to update instead of matching by username.
        :param acct_role: new Redfish RoleId, or None to leave unchanged.
        :param acct_password: new password, or None to leave unchanged.
        :param acct_enabled: ``true``/``false`` string to enable or disable the
            account, or None to leave unchanged.
        :param acct_confirm: when False (default) return a masked dry-run
            preview; when True PATCH the account resource.
        :return: CommandResult with the dry-run preview or the update result;
            an error when no target/field is given or the account is not found.
        """
        if not acct_user and not acct_id:
            return CommandResult(None, None, None, "provide --username or --id")
        enabled = None
        if acct_enabled is not None:
            enabled = str(acct_enabled).strip().lower() in ("1", "true", "yes", "on")
        payload = build_update_payload(acct_password, acct_role, enabled)
        if not payload:
            return CommandResult(None, None, None, "nothing to update (set --role/--password/--enabled)")
        uri, data = self._resolve_account(acct_user, acct_id)
        if not uri:
            return CommandResult(None, None, None, f"account not found: {acct_user or acct_id}")
        if not acct_confirm:
            return CommandResult(
                {"dry_run": True, "action": "update", "target": data.get("UserName"),
                 "uri": uri, "payload": _mask(payload)}, None, None, None)
        res, status = self.base_patch(uri, payload=payload)
        return CommandResult(
            {"action": "update", "target": data.get("UserName"), "status": str(status),
             "error": res.error}, None, None, res.error)


class AccountDelete(_AccountBase,
                    scm_type=ApiRequestType.AccountDelete,
                    name='account-delete',
                    metaclass=Singleton):
    """Delete a Redfish account (refuses without --confirm; never self-deletes)."""

    def __init__(self, *args, **kwargs):
        """Initialize the account-delete command."""
        super(AccountDelete, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``account-delete`` subcommand and its flags."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--username', required=False, type=str, dest='acct_user', default=None)
        cmd_parser.add_argument('--id', required=False, type=str, dest='acct_id', default=None)
        cmd_parser.add_argument('--confirm', action='store_true', required=False,
                                dest='acct_confirm', default=False,
                                help="required to actually delete (irreversible)")
        return cmd_parser, "account-delete", "delete a Redfish account (guarded, irreversible)"

    def execute(self, acct_user=None, acct_id=None, acct_confirm=False, **kwargs) -> CommandResult:
        """Delete a Redfish account (guarded, never self-deletes).

        :param acct_user: UserName of the account to delete (or use acct_id).
        :param acct_id: account Id to delete instead of matching by username.
        :param acct_confirm: when False (default) return a dry-run preview;
            when True DELETE the account resource (irreversible).
        :return: CommandResult with the dry-run preview or delete result; an
            error when no target is given, the account is not found, or the
            target is the logged-in account (self-delete guard).
        """
        if not acct_user and not acct_id:
            return CommandResult(None, None, None, "provide --username or --id")
        uri, data = self._resolve_account(acct_user, acct_id)
        if not uri:
            return CommandResult(None, None, None, f"account not found: {acct_user or acct_id}")
        target = data.get("UserName")
        # Never delete the account we authenticated as — that could lock the box out.
        if target and target == self._username:
            return CommandResult(
                None, None, None,
                f"refusing to delete the logged-in account '{target}' (self-delete guard)")
        if not acct_confirm:
            return CommandResult(
                {"dry_run": True, "action": "delete", "target": target, "uri": uri,
                 "hint": "re-run with --confirm to delete (irreversible)"}, None, None, None)
        res, status = self.base_delete(uri)
        return CommandResult(
            {"action": "delete", "target": target, "status": str(status),
             "error": res.error}, None, None, res.error)
