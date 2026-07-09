"""Read Redfish log service entries (system event / IML / SEL / manager logs).

    redfish_ctl logs
    redfish_ctl logs --limit 20

Walks every ComputerSystem and Manager, follows their ``LogServices`` collection
-> each log service -> its ``Entries``, flattening to {Source, Service, Id,
Severity, Created, Message}. Navigation is by link/``@odata.id`` with no hardcoded
ids, so it reads Dell (SEL/Lclog), HPE iLO (IML/SL/Event/IEL), Supermicro, etc.

Entries are capped per service (``--limit``) because a real box can carry
hundreds (an iLO IML alone has ~700).

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class Logs(IDracManager,
          scm_type=ApiRequestType.Logs,
          name='logs',
          metaclass=Singleton):
    """Read log-service entries from every system and manager."""

    def __init__(self, *args, **kwargs):
        super(Logs, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``logs`` subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--limit', required=False, dest='limit', type=int, default=50,
            help="max entries per log service (default 50)")
        return cmd_parser, "logs", "command read system/manager log service entries"

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

    def _roots(self):
        """Every ComputerSystem + Manager + Chassis URI, multi-member aware.

        Log services hang off different roots per vendor — Systems/Managers on
        iLO, Chassis on the GB300 — so all three are walked.
        """
        roots = []
        for finder in (self.discover_computer_system_ids, self.discover_manager_ids):
            try:
                roots.extend(finder() or [])
            except Exception:
                continue
        try:
            roots.extend(self._members(self._get(f"{RedfishApi.Version}/Chassis", False)))
        except Exception:
            pass
        return roots

    def execute(self,
                limit: Optional[int] = 50,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk LogServices on every system/manager and collect capped entries."""
        rows = []
        for root_uri in self._roots():
            rdata = self._get(root_uri, do_async)
            services_uri = self._link(rdata, "LogServices")
            if not services_uri:
                continue
            for svc_uri in self._members(self._get(services_uri, do_async)):
                svc = self._get(svc_uri, do_async)
                svc_id = svc.get("Id") or svc_uri.rsplit("/", 1)[-1]
                entries_uri = self._link(svc, "Entries")
                if not entries_uri:
                    continue
                coll = self._get(entries_uri, do_async)
                for member in self._members(coll)[:max(0, limit or 0)]:
                    entry = self._get(member, do_async)
                    if not isinstance(entry, dict):
                        continue
                    rows.append({
                        "Source": root_uri.rsplit("/", 1)[-1],
                        "Service": svc_id,
                        "Id": entry.get("Id"),
                        "Severity": entry.get("Severity"),
                        "Created": entry.get("Created"),
                        "Message": entry.get("Message"),
                    })
        return CommandResult(rows, None, None, None)
