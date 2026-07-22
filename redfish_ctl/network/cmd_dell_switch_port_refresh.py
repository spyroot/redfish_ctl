"""Refresh Dell switch-connection view metadata.

    redfish_ctl dell-switch-port-refresh
    redfish_ctl dell-switch-port-refresh --dry_run
    redfish_ctl dell-switch-port-refresh --confirm

The command resolves Dell's ``DellSwitchConnectionService`` from discovered
ComputerSystem resources and lists the target by default. ``--dry_run`` previews
the refresh POST, and ``--confirm`` is required before the POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SWITCH_REFRESH_ACTION = "#DellSwitchConnectionService.ServerPortConnectionRefresh"
_SWITCH_REFRESH_NAME = "ServerPortConnectionRefresh"
_SYSTEM_FALLBACK = f"{RedfishApi.Version}/Systems/System.Embedded.1"
_SERVICE_SUFFIX = "Oem/Dell/DellSwitchConnectionService"
_CONNECTIONS_SUFFIX = "NetworkPorts/Oem/Dell/DellSwitchConnections"
_SERVICE_FALLBACK = f"{_SYSTEM_FALLBACK}/{_SERVICE_SUFFIX}"
_CONNECTIONS_FALLBACK = f"{_SYSTEM_FALLBACK}/{_CONNECTIONS_SUFFIX}"


class DellSwitchPortRefresh(
    IDracManager,
    scm_type=ApiRequestType.DellSwitchPortRefresh,
    name="dell-switch-port-refresh",
    metaclass=Singleton,
):
    """Refresh Dell switch-connection view data through DellSwitchConnectionService."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-switch-port-refresh command."""
        super(DellSwitchPortRefresh, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-switch-port-refresh`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--service-uri",
            dest="service_uri",
            default=None,
            help="specific DellSwitchConnectionService URI when more than one exists",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the refresh action instead of listing the target",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the refresh target without POSTing",
        )
        return (
            cmd_parser,
            "dell-switch-port-refresh",
            "command refresh Dell switch-connection view metadata",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish object that may contain a link property.
        :param key: property name whose ``@odata.id`` should be returned.
        :return: linked Redfish URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _links_oem_dell(data):
        """Return the ``Links.Oem.Dell`` block from a Redfish object.

        :param data: Redfish object that may contain Dell OEM links.
        :return: Dell OEM links block, or an empty dict when absent.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI.
        :param do_async: issue the query over the async path when True.
        :param optional: return an empty object instead of failing on read errors.
        :return: parsed resource body.
        :raises InvalidArgument: when a required read fails or is not an object.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            if optional:
                return {}
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            if optional:
                return {}
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    def _system_uris(self):
        """Return discovered ComputerSystem URIs with the Dell fallback last.

        :return: de-duplicated ComputerSystem URI candidates.
        """
        try:
            system_uris = list(self.discover_computer_system_ids() or [])
        except Exception:
            system_uris = []
        system_uris.append(_SYSTEM_FALLBACK)
        seen = set()
        ordered = []
        for uri in system_uris:
            if uri and uri not in seen:
                seen.add(uri)
                ordered.append(uri)
        return ordered

    def _service_candidates(self, do_async):
        """Return DellSwitchConnectionService URI candidates.

        :param do_async: issue supporting queries over the async path when True.
        :return: list of dictionaries with system, service, and connection URIs.
        """
        candidates = []
        for system_uri in self._system_uris():
            system_uri = system_uri.rstrip("/")
            system = self._get(system_uri, do_async, optional=True)
            linked = self._link(
                self._links_oem_dell(system),
                "DellSwitchConnectionService",
            )
            service_uri = linked or f"{system_uri}/{_SERVICE_SUFFIX}"
            candidates.append({
                "System": system_uri.rsplit("/", 1)[-1],
                "SystemUri": system_uri,
                "Service": service_uri,
                "ConnectionCollection": f"{system_uri}/{_CONNECTIONS_SUFFIX}",
            })
        candidates.append({
            "System": "System.Embedded.1",
            "SystemUri": _SYSTEM_FALLBACK,
            "Service": _SERVICE_FALLBACK,
            "ConnectionCollection": _CONNECTIONS_FALLBACK,
        })

        seen = set()
        ordered = []
        for candidate in candidates:
            service_uri = candidate["Service"]
            if service_uri not in seen:
                seen.add(service_uri)
                ordered.append(candidate)
        return ordered

    def _connection_summary(self, collection_uri, do_async):
        """Return a compact summary of DellSwitchConnection collection state.

        :param collection_uri: DellSwitchConnections collection URI.
        :param do_async: issue the collection query over the async path when True.
        :return: compact dictionary with URI, member count, and stale states.
        """
        collection = self._get(collection_uri, do_async, optional=True)
        members = collection.get("Members") if isinstance(collection, dict) else None
        if not isinstance(members, list):
            members = []
        count = collection.get("Members@odata.count")
        if count is None:
            count = len(members)
        stale_states = sorted({
            str(member.get("StaleData"))
            for member in members
            if isinstance(member, dict) and member.get("StaleData") is not None
        })
        return {
            "Uri": collection_uri,
            "Count": count,
            "StaleData": stale_states,
        }

    def _discover_rows(self, do_async):
        """Discover Dell switch refresh targets from ComputerSystem resources.

        :param do_async: issue discovery queries over the async path when True.
        :return: list of discovered refresh target rows.
        """
        rows = []
        for candidate in self._service_candidates(do_async):
            service = self._get(candidate["Service"], do_async, optional=True)
            if not service:
                continue
            target = self._flatten_action_targets(service).get(_SWITCH_REFRESH_ACTION)
            if not target:
                continue
            rows.append({
                "System": candidate["System"],
                "SystemUri": candidate["SystemUri"],
                "Service": candidate["Service"],
                "Target": target,
                "Connections": self._connection_summary(
                    candidate["ConnectionCollection"],
                    do_async,
                ),
            })
        return rows

    @staticmethod
    def _select_rows(rows, service_uri):
        """Filter discovered rows by optional service URI.

        :param rows: discovered refresh target rows.
        :param service_uri: optional DellSwitchConnectionService URI selector.
        :return: matching rows.
        :raises InvalidArgument: when the selector matches no discovered row.
        """
        if not service_uri:
            return rows
        normalized = service_uri.rstrip("/")
        matches = [
            row for row in rows
            if row["Service"].rstrip("/") == normalized
        ]
        if not matches:
            available = [row["Service"] for row in rows]
            raise InvalidArgument(
                f"no DellSwitchConnectionService target for '{service_uri}'; "
                f"available: {available}"
            )
        return matches

    def execute(
        self,
        service_uri: Optional[str] = None,
        confirm: Optional[bool] = False,
        dry_run: Optional[bool] = False,
        filename: Optional[str] = None,
        data_type: Optional[str] = "json",
        verbose: Optional[bool] = False,
        do_async: Optional[bool] = False,
        **kwargs,
    ) -> CommandResult:
        """List, preview, or invoke Dell switch-connection refresh.

        :param service_uri: optional DellSwitchConnectionService URI selector.
        :param confirm: send the refresh POST when True.
        :param dry_run: resolve the refresh target without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POST on the async path.
        :return: CommandResult with target listing, preview, execution, or error.
        """
        rows = self._select_rows(self._discover_rows(bool(do_async)), service_uri)
        if not rows:
            return CommandResult(
                {"action": _SWITCH_REFRESH_ACTION, "available": []},
                None,
                None,
                "DellSwitchConnectionService.ServerPortConnectionRefresh "
                "action not found",
            )
        if not confirm and not dry_run:
            return CommandResult(rows, None, None, None)
        if len(rows) > 1:
            services = [row["Service"] for row in rows]
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple Dell switch refresh targets found; pass --service-uri "
                f"with one of: {services}",
            )

        row = rows[0]
        result = self.invoke_action(
            row["Service"],
            _SWITCH_REFRESH_NAME,
            payload={},
            full_action_type=_SWITCH_REFRESH_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if dry_run and isinstance(result.data, dict):
            result.data["service"] = row["Service"]
            result.data["connections"] = row["Connections"]
        return result
