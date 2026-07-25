"""Read Redfish SecureBoot state + key databases (PK/KEK/db/dbx).

    redfish_ctl secure-boot
    redfish_ctl secure-boot-reset-keys
    redfish_ctl secure-boot-reset-keys --reset-type ResetAllKeysToDefault
    redfish_ctl secure-boot-reset-keys --database PK --reset-type DeleteAllKeys --confirm

For every ComputerSystem, reads ``Systems/{id}/SecureBoot`` (enable / mode /
current-boot) and walks its ``SecureBootDatabases`` collection, returning one row
per database {System, SecureBootEnable, SecureBootMode, Database, DatabaseId,
Certificates}. A system with SecureBoot but no databases still yields a state row.

Navigation is by link/``@odata.id`` with no hardcoded ids; standard Redfish, so
it works on Dell, HPE iLO, Supermicro, etc.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_SECURE_BOOT_RESET_ACTION = "#SecureBoot.ResetKeys"
_SECURE_BOOT_DATABASE_RESET_ACTION = "#SecureBootDatabase.ResetKeys"


class SecureBoot(IDracManager,
                 scm_type=ApiRequestType.SecureBoot,
                 name='secure-boot',
                 metaclass=Singleton):
    """Read SecureBoot state and key databases for every system."""

    def __init__(self, *args, **kwargs):
        """Initialize the secure-boot command."""
        super(SecureBoot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``secure-boot`` subcommand (read-only).

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read SecureBoot state and key databases (PK/KEK/db/dbx)"
        return cmd_parser, "secure-boot", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: Redfish collection body (or any value).
        :return: list of member ``@odata.id`` strings; empty list if not a collection.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource path to fetch.
        :param do_async: when set, the query subscribes to an event loop for async execution.
        :return: the resource body as a dict, or ``{}`` on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _link(data, key):
        """Return the @odata.id of a single ``{key: {@odata.id}}`` link, or None.

        :param data: resource body holding the link (or any value).
        :param key: name of the link property to read.
        :return: the linked ``@odata.id`` string, or None when absent.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _cert_count(self, db, do_async):
        """Number of certificates in a SecureBootDatabase, tolerantly.

        :param db: SecureBootDatabase body.
        :param do_async: when set, the certificate query subscribes to an event loop for async execution.
        :return: certificate count, or None when the database has no Certificates link.
        """
        coll_uri = self._link(db, "Certificates")
        if not coll_uri:
            return None
        return len(self._members(self._get(coll_uri, do_async)))

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read SecureBoot state + databases for every discovered system.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when set, each Redfish query subscribes to an event loop for async execution.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping one SecureBoot state row per system/database.
        """
        rows = []
        try:
            system_ids = self.discover_computer_system_ids() or []
        except Exception:
            system_ids = []
        for system_uri in system_ids:
            sb = self._get(f"{system_uri}/SecureBoot", do_async)
            if not sb:
                continue
            base = {
                "System": system_uri.rsplit("/", 1)[-1],
                "SecureBootEnable": sb.get("SecureBootEnable"),
                "SecureBootMode": sb.get("SecureBootMode"),
                "SecureBootCurrentBoot": sb.get("SecureBootCurrentBoot"),
            }
            dbs_uri = self._link(sb, "SecureBootDatabases")
            db_uris = self._members(self._get(dbs_uri, do_async)) if dbs_uri else []
            if not db_uris:
                rows.append({**base, "Database": None, "DatabaseId": None, "Certificates": None})
                continue
            for db_uri in db_uris:
                db = self._get(db_uri, do_async)
                rows.append({
                    **base,
                    "Database": db.get("Id") or db_uri.rsplit("/", 1)[-1],
                    "DatabaseId": db.get("DatabaseId"),
                    "Certificates": self._cert_count(db, do_async),
                })
        return CommandResult(rows, None, None, None)


class SecureBootResetKeys(IDracManager,
                          scm_type=ApiRequestType.SecureBootResetKeys,
                          name="secure-boot-reset-keys",
                          metaclass=Singleton):
    """Preview or run guarded SecureBoot ResetKeys actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the secure-boot-reset-keys command."""
        super(SecureBootResetKeys, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``secure-boot-reset-keys`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--system",
            dest="system",
            default=None,
            help="ComputerSystem id or URI; omit when only one target matches",
        )
        cmd_parser.add_argument(
            "--database",
            dest="database",
            default=None,
            help="SecureBootDatabase id or URI; omit to target SecureBoot",
        )
        cmd_parser.add_argument(
            "--reset-type",
            dest="reset_type",
            default=None,
            help="ResetKeysType value advertised by the selected target",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="POST the reset action; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="force a preview even when --confirm is present",
        )
        return (
            cmd_parser,
            "secure-boot-reset-keys",
            "command reset SecureBoot keys through guarded Redfish actions",
        )

    def _get(self, uri, do_async):
        """GET a Redfish object body or fail with a clear CLI error.

        :param uri: Redfish URI to fetch.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body.
        :raises InvalidArgument: when the resource cannot be read or is not an object.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    @staticmethod
    def _link(data, key):
        """Return a Redfish ``@odata.id`` link value.

        :param data: object that may contain a Redfish link.
        :param key: link property name.
        :return: linked URI or None.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _members(data):
        """Return collection member URIs from a Redfish collection body.

        :param data: Redfish collection body.
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _action_metadata(data, action_name):
        """Return an action target and ResetKeysType allowlist.

        :param data: Redfish resource body with an ``Actions`` block.
        :param action_name: full Redfish action name to inspect.
        :return: tuple of ``(target, reset_types)``.
        """
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(action_name) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return None, []
        target = action.get("target")
        values = action.get("ResetKeysType@Redfish.AllowableValues") or []
        if not isinstance(values, list):
            values = []
        return target, [value for value in values if isinstance(value, str)]

    def _database_rows(self, system_uri, secure_boot, do_async):
        """Discover SecureBootDatabase reset targets below one SecureBoot resource.

        :param system_uri: parent ComputerSystem URI.
        :param secure_boot: SecureBoot resource body.
        :param do_async: issue the queries on the async path when True.
        :return: list of database reset target rows.
        """
        dbs_uri = self._link(secure_boot, "SecureBootDatabases")
        if not dbs_uri:
            return []
        rows = []
        for db_uri in self._members(self._get(dbs_uri, do_async)):
            try:
                db = self._get(db_uri, do_async)
            except InvalidArgument:
                continue
            target, reset_types = self._action_metadata(
                db,
                _SECURE_BOOT_DATABASE_RESET_ACTION,
            )
            if not target:
                continue
            rows.append({
                "System": system_uri.rsplit("/", 1)[-1],
                "SystemUri": system_uri,
                "Scope": "database",
                "Database": db.get("Id") or db_uri.rsplit("/", 1)[-1],
                "DatabaseId": db.get("DatabaseId"),
                "Resource": db_uri,
                "Action": _SECURE_BOOT_DATABASE_RESET_ACTION,
                "Target": target,
                "ResetKeysType": reset_types,
            })
        return rows

    def _discover_rows(self, do_async):
        """Discover SecureBoot and SecureBootDatabase reset targets.

        :param do_async: issue discovery reads on the async path when True.
        :return: list of discovered reset target rows.
        """
        rows = []
        try:
            system_uris = self.discover_computer_system_ids() or []
        except Exception:
            system_uris = []
        for system_uri in system_uris:
            secure_boot_uri = f"{system_uri}/SecureBoot"
            secure_boot = self._get(secure_boot_uri, do_async)
            target, reset_types = self._action_metadata(
                secure_boot,
                _SECURE_BOOT_RESET_ACTION,
            )
            if target:
                rows.append({
                    "System": system_uri.rsplit("/", 1)[-1],
                    "SystemUri": system_uri,
                    "Scope": "secure-boot",
                    "Database": None,
                    "DatabaseId": None,
                    "Resource": secure_boot_uri,
                    "Action": _SECURE_BOOT_RESET_ACTION,
                    "Target": target,
                    "ResetKeysType": reset_types,
                })
            rows.extend(self._database_rows(system_uri, secure_boot, do_async))
        return rows

    @staticmethod
    def _match_system(row, system):
        """Return whether a discovered row matches a system selector.

        :param row: discovered reset target row.
        :param system: optional system id or URI.
        :return: True when the row matches.
        """
        if system is None:
            return True
        wanted = system.rstrip("/")
        return wanted in {
            row["System"],
            row["SystemUri"].rstrip("/"),
            row["Resource"].rsplit("/SecureBoot", 1)[0].rstrip("/"),
        }

    @staticmethod
    def _match_database(row, database):
        """Return whether a discovered row matches a database selector.

        :param row: discovered reset target row.
        :param database: optional database id or URI.
        :return: True when the row matches.
        """
        if database is None:
            return row["Scope"] == "secure-boot"
        wanted = database.rstrip("/")
        return row["Scope"] == "database" and wanted in {
            str(row["Database"]),
            str(row["DatabaseId"]),
            row["Resource"].rstrip("/"),
        }

    def _select_row(self, rows, system, database):
        """Resolve CLI selectors to one reset target row.

        :param rows: discovered reset target rows.
        :param system: optional system selector.
        :param database: optional database selector.
        :return: selected reset target row.
        :raises InvalidArgument: when no target or multiple targets match.
        """
        matches = [
            row for row in rows
            if self._match_system(row, system) and self._match_database(row, database)
        ]
        if not matches:
            raise InvalidArgument(
                "no SecureBoot reset target matched the supplied selectors"
            )
        if len(matches) > 1:
            raise InvalidArgument(
                "multiple SecureBoot reset targets matched; pass --system"
            )
        return matches[0]

    @staticmethod
    def _payload(row, reset_type):
        """Build and validate a ResetKeys payload.

        :param row: selected reset target row.
        :param reset_type: requested ResetKeysType value.
        :return: payload for the Redfish action.
        :raises InvalidArgument: when reset_type is absent or not advertised.
        """
        value = (reset_type or "").strip()
        if not value:
            raise InvalidArgument("reset type is required to run a reset action")
        allowed = row.get("ResetKeysType") or []
        if allowed and value not in allowed:
            raise InvalidArgument(
                f"invalid ResetKeysType {value}; allowed: {', '.join(allowed)}"
            )
        return {"ResetKeysType": value}

    def execute(self,
                system: Optional[str] = None,
                database: Optional[str] = None,
                reset_type: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke guarded SecureBoot ResetKeys actions.

        :param system: optional ComputerSystem id or URI selector.
        :param database: optional SecureBootDatabase id or URI selector.
        :param reset_type: ResetKeysType value to send.
        :param confirm: authorize the destructive POST when True.
        :param dry_run: force a preview even when confirm is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue Redfish reads and POST on the async path.
        :return: CommandResult with discovered targets, dry-run, POST result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if reset_type is None:
            return CommandResult(rows, None, None, None)

        row = self._select_row(rows, system, database)
        return self.invoke_action(
            row["Resource"],
            "ResetKeys",
            payload=self._payload(row, reset_type),
            full_action_type=row["Action"],
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
