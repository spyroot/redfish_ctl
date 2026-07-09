"""Create, update, and delete Redfish accounts (ManagerAccount).

    idrac_ctl account-create --username test --password '***' --role ReadOnly --confirm
    idrac_ctl account-update --username test --role Operator --confirm
    idrac_ctl account-delete --username test --confirm

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

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult

# RoleId is the standard Redfish privilege model (present on Dell/HPE/Supermicro).
DEFAULT_ROLE = "ReadOnly"


def build_create_payload(username: str, password: str, role: str) -> dict:
    """Build the POST body that creates a ManagerAccount."""
    return {"UserName": username, "Password": password, "RoleId": role or DEFAULT_ROLE}


def build_update_payload(password: Optional[str],
                         role: Optional[str],
                         enabled: Optional[bool]) -> dict:
    """Build a PATCH body from only the fields the caller actually set."""
    payload: dict = {}
    if password is not None:
        payload["Password"] = password
    if role is not None:
        payload["RoleId"] = role
    if enabled is not None:
        payload["Enabled"] = enabled
    return payload


def _mask(payload: dict) -> dict:
    """Return a copy of a payload with the Password value masked for display."""
    return {k: ("***" if k == "Password" else v) for k, v in payload.items()}


class _AccountBase(IDracManager):
    """Shared account resolution for the write commands."""

    def _resolve_account(self, username: Optional[str],
                         account_id: Optional[str]) -> Tuple[Optional[str], dict]:
        """Find an account by UserName or Id; return (uri, data) or (None, {}).

        Reads the Accounts collection and each member (vendor-neutral — it never
        assumes an id scheme), so it matches on the real ``UserName``/``Id``. The
        collection is fetched WITHOUT ``$expand`` because some services (HPE iLO 5)
        reject an expanded Accounts query with HTTP 400; each member is fetched
        individually instead.
        """
        coll = (self.base_query(IDRAC_API.Accounts).data or {})
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
        super(AccountCreate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
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
        payload = build_create_payload(acct_user, acct_password, acct_role)
        if not acct_confirm:
            return CommandResult(
                {"dry_run": True, "action": "create", "payload": _mask(payload),
                 "hint": "re-run with --confirm to create"}, None, None, None)
        res, status = self.base_post(IDRAC_API.Accounts, payload=payload, expected_status=201)
        return CommandResult(
            {"action": "create", "username": acct_user, "role": acct_role,
             "status": str(status), "error": res.error}, None, None, res.error)


class AccountUpdate(_AccountBase,
                    scm_type=ApiRequestType.AccountUpdate,
                    name='account-update',
                    metaclass=Singleton):
    """Update a Redfish account's password/role/enabled (dry-run unless --confirm)."""

    def __init__(self, *args, **kwargs):
        super(AccountUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
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
        super(AccountDelete, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--username', required=False, type=str, dest='acct_user', default=None)
        cmd_parser.add_argument('--id', required=False, type=str, dest='acct_id', default=None)
        cmd_parser.add_argument('--confirm', action='store_true', required=False,
                                dest='acct_confirm', default=False,
                                help="required to actually delete (irreversible)")
        return cmd_parser, "account-delete", "delete a Redfish account (guarded, irreversible)"

    def execute(self, acct_user=None, acct_id=None, acct_confirm=False, **kwargs) -> CommandResult:
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
