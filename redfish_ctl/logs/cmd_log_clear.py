"""Clear a Redfish log service (LogService.ClearLog).

    redfish_ctl log-clear                                # list clearable log services (no mutation)
    redfish_ctl log-clear --log-service Sel --confirm    # clear the Dell SEL
    redfish_ctl log-clear --log-service IML --dry_run     # preview the HPE iLO IML clear

Discovers every LogService under ComputerSystems, Managers, and Chassis by link
(no hardcoded ids), so it works on Dell (Sel/Lclog), HPE iLO (IML/IEL), Supermicro,
and the GB300 (Dump). Clearing a log destroys its entries, so the ClearLog POST is
DESTRUCTIVE: without ``--confirm`` the command only previews (dry-run). The action
target is discovered from the LogService's own ``Actions`` block via the shared
``invoke_action`` primitive, which enforces the destructiveness guard.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_CLEAR_LOG_ACTION = "#LogService.ClearLog"


class LogClear(IDracManager,
               scm_type=ApiRequestType.LogClear,
               name="log-clear",
               metaclass=Singleton):
    """Clear a discovered LogService via LogService.ClearLog."""

    def __init__(self, *args, **kwargs):
        """Initialize the log-clear command."""
        super(LogClear, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``log-clear`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--log-service", required=False, dest="log_service", type=str,
            default=None,
            help="LogService Id (e.g. Sel, IML, Log) or its full URI to clear; "
                 "omit to list the clearable log services without mutating")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="fire the ClearLog POST; without it the command only previews")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="resolve the target and show it without POSTing")
        return cmd_parser, "log-clear", "command clear a Redfish log service"

    @staticmethod
    def _members(data):
        """Return the ``@odata.id`` strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (or any value; non-dicts yield []).
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    @staticmethod
    def _link(data, key):
        """Return the ``@odata.id`` of a single ``{key: {@odata.id}}`` link, or None.

        :param data: the resource body holding the link (may be None).
        :param key: the property name whose ``@odata.id`` to extract.
        :return: the link target URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async event loop when True.
        :return: the parsed response body, or {} when the query fails.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _roots(self, do_async):
        """Every ComputerSystem + Manager + Chassis URI, multi-member aware.

        Log services hang off different roots per vendor — Systems/Managers on
        iLO, Chassis on the GB300 — so all three collections are walked.

        :param do_async: issue the Chassis collection query on the async loop when True.
        :return: list of root resource URIs to inspect for log services.
        """
        roots = []
        for finder in (self.discover_computer_system_ids, self.discover_manager_ids):
            try:
                roots.extend(finder() or [])
            except Exception:
                continue
        try:
            roots.extend(self._members(self._get(f"{RedfishApi.Version}/Chassis", do_async)))
        except Exception:
            pass
        return roots

    def _discover_log_services(self, do_async):
        """Discover every clearable LogService across all roots.

        Walks each root's ``LogServices`` collection and keeps the services whose
        ``Actions`` block exposes ``#LogService.ClearLog``.

        :param do_async: issue the underlying queries on the async loop when True.
        :return: list of ``{"Id": <id>, "uri": <service uri>}`` dicts, de-duplicated
            by service URI and ordered by discovery.
        """
        services = []
        seen = set()
        for root_uri in self._roots(do_async):
            services_uri = self._link(self._get(root_uri, do_async), "LogServices")
            if not services_uri:
                continue
            for svc_uri in self._members(self._get(services_uri, do_async)):
                if svc_uri in seen:
                    continue
                svc = self._get(svc_uri, do_async)
                actions = svc.get("Actions") if isinstance(svc, dict) else None
                if not isinstance(actions, dict) or _CLEAR_LOG_ACTION not in actions:
                    continue
                seen.add(svc_uri)
                services.append({
                    "Id": svc.get("Id") or svc_uri.rsplit("/", 1)[-1],
                    "uri": svc_uri,
                })
        return services

    @staticmethod
    def _resolve_target(log_service, services):
        """Resolve the requested LogService to a service URI.

        :param log_service: a LogService Id (case-insensitive) or a full Redfish URI.
        :param services: the discovered clearable services from
            :meth:`_discover_log_services`.
        :return: the resolved LogService URI.
        :raises InvalidArgument: when the id/URI matches no clearable service.
        """
        if log_service.startswith("/redfish"):
            match = next((s for s in services if s["uri"] == log_service), None)
            if match is not None:
                return match["uri"]
            raise InvalidArgument(
                f"no clearable log service at URI '{log_service}'; "
                f"available: {[s['uri'] for s in services]}")
        wanted = log_service.strip().lower()
        matches = [s for s in services if s["Id"].lower() == wanted]
        if not matches:
            raise InvalidArgument(
                f"no clearable log service with Id '{log_service}'; "
                f"available: {[s['Id'] for s in services]}")
        if len(matches) > 1:
            raise InvalidArgument(
                f"log service Id '{log_service}' is ambiguous across "
                f"{[s['uri'] for s in matches]}; pass the full --log-service URI")
        return matches[0]["uri"]

    def execute(self,
                log_service: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List clearable log services, or clear one via LogService.ClearLog.

        With no ``--log-service`` the command discovers and returns the clearable
        services WITHOUT mutating. With ``--log-service`` it resolves the target and
        invokes ClearLog; because ClearLog is DESTRUCTIVE, the POST only fires with
        ``--confirm`` (the guard is enforced inside ``invoke_action``).

        :param log_service: LogService Id or full URI to clear; None lists services.
        :param confirm: authorize the DESTRUCTIVE ClearLog POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async loop when True.
        :return: a CommandResult whose data is the clearable-service list (when no
            target is given), the ClearLog outcome, or the dry-run/blocked preview.
        :raises InvalidArgument: when ``log_service`` matches no clearable service.
        """
        services = self._discover_log_services(do_async)
        if log_service is None:
            return CommandResult(
                {"clearable_log_services": services}, None, None, None)
        if not services:
            raise InvalidArgument(
                "no clearable log services found on this Redfish endpoint "
                "(none expose #LogService.ClearLog)")

        target_uri = self._resolve_target(log_service, services)
        # ClearLog is DMTF-typically 204 No Content, but read_api_respond accepts
        # any 2xx as success, so a box that answers 200/202 is not a false error.
        return self.invoke_action(
            target_uri,
            "ClearLog",
            payload={},
            full_action_type=_CLEAR_LOG_ACTION,
            do_async=do_async,
            expected_status=204,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
