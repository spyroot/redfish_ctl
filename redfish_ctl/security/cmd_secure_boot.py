"""Read Redfish SecureBoot state + key databases (PK/KEK/db/dbx).

    redfish_ctl secure-boot

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

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class SecureBoot(RedfishManagerBase,
                 scm_type=ApiRequestType.SecureBoot,
                 name='secure-boot',
                 metaclass=Singleton):
    """Read SecureBoot state and key databases for every system."""

    def __init__(self, *args, **kwargs):
        super(SecureBoot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``secure-boot`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command read SecureBoot state and key databases (PK/KEK/db/dbx)"
        return cmd_parser, "secure-boot", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly."""
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure."""
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _link(data, key):
        """Return the @odata.id of a single ``{key: {@odata.id}}`` link, or None."""
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _cert_count(self, db, do_async):
        """Number of certificates in a SecureBootDatabase, tolerantly."""
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
        """Read SecureBoot state + databases for every discovered system."""
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
