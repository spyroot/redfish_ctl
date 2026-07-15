"""Set ManagerNetworkProtocol NTP servers with a guarded PATCH.

    redfish_ctl ntp-set
"""

import ipaddress
import re
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_MAX_NTP_SERVERS = 4
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _normalize_ntp_servers(servers, clear: bool = False) -> list[str]:
    """Normalize and validate NTP server arguments into a clean list.

    :param servers: one server string, a comma-joined string, or a list of them.
    :param clear: request an empty NTP list; rejects any provided ``servers``.
    :return: the normalized list of NTP servers (empty when ``clear`` is set).
    :raises InvalidArgument: when ``clear`` is combined with servers, no server is
        given, more than four are given, or a server is not plausible.
    """
    if clear:
        if servers:
            raise InvalidArgument("--clear cannot be used with --server")
        return []
    if servers is None:
        raise InvalidArgument("at least one NTP server is required")
    if isinstance(servers, str):
        raw_servers = [servers]
    else:
        raw_servers = list(servers)

    normalized = []
    for raw in raw_servers:
        for value in str(raw).split(","):
            server = value.strip()
            if server:
                normalized.append(server)

    if not normalized:
        raise InvalidArgument("at least one NTP server is required")
    if len(normalized) > _MAX_NTP_SERVERS:
        raise InvalidArgument("ManagerNetworkProtocol NTP supports at most 4 servers")
    for server in normalized:
        if not _is_plausible_ntp_server(server):
            raise InvalidArgument(f"invalid NTP server: {server!r}")
    return normalized


def _is_plausible_ntp_server(server: str) -> bool:
    """Check whether a string is a plausible NTP IP address or hostname.

    :param server: the candidate NTP server value.
    :return: True when it parses as an IP or a valid dotted hostname, else False.
    """
    if not server or any(ch.isspace() for ch in server):
        return False
    if "://" in server or "/" in server:
        return False
    try:
        ipaddress.ip_address(server)
        return True
    except ValueError:
        pass

    hostname = server[:-1] if server.endswith(".") else server
    if len(hostname) > 253 or not hostname:
        return False
    labels = hostname.split(".")
    return all(_HOST_LABEL.fullmatch(label) for label in labels)


class NtpSet(RedfishManagerBase,
             scm_type=ApiRequestType.NtpSet,
             name='ntp-set',
             metaclass=Singleton):
    """Set ManagerNetworkProtocol NTP servers after dry-run preview."""

    def __init__(self, *args, **kwargs):
        """Initialize the ntp-set command."""
        super(NtpSet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ntp-set subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--server', action='append', dest='servers', metavar='HOST',
            help="NTP hostname or IP; repeat for up to 4 servers")
        cmd_parser.add_argument(
            '--clear', action='store_true', dest='clear', default=False,
            help="restore an empty NTP server list")
        cmd_parser.add_argument(
            '--manager', type=str, dest='manager_id', default=None, metavar='ID',
            help="optional Manager id to patch; default patches NTP-capable managers")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm', default=False,
            help="apply the PATCH; without it the command only previews")
        help_text = "set ManagerNetworkProtocol NTP servers"
        return cmd_parser, "ntp-set", help_text

    @staticmethod
    def _link(data, key):
        """Return the ``@odata.id`` of a link field, or None when absent.

        :param data: the resource body to read the link from.
        :param key: the link field name (e.g. ``NetworkProtocol``).
        :return: the linked resource URI, or None when the field is not a link.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource URI to query.
        :param do_async: run the query asynchronously (subscribes to the event loop).
        :return: the resource body dict, or {} on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _ntp_plan(self, servers, manager_id, do_async):
        """Build the per-manager PATCH plan for the requested NTP servers.

        :param servers: the normalized NTP servers to set (empty to clear).
        :param manager_id: optional Manager id to restrict the plan to.
        :param do_async: run the discovery queries asynchronously.
        :return: tuple of (plan, skipped) — the targets to PATCH and the managers
            skipped with a reason.
        :raises InvalidArgument: when no NTP-capable NetworkProtocol matches.
        """
        ntp_payload = {"NTPServers": servers}
        if servers:
            ntp_payload["ProtocolEnabled"] = True
        payload = {"NTP": ntp_payload}
        plan = []
        skipped = []

        for manager_uri in self.discover_manager_ids():
            manager = self._get(manager_uri, do_async)
            current_manager_id = manager.get("Id") or manager_uri.rsplit("/", 1)[-1]
            network_uri = self._link(manager, "NetworkProtocol")
            if manager_id and current_manager_id != manager_id:
                continue
            if not network_uri:
                skipped.append({
                    "Manager": current_manager_id,
                    "target": None,
                    "reason": "NetworkProtocol link is not available",
                })
                continue
            network = self._get(network_uri, do_async)
            if not isinstance(network.get("NTP"), dict):
                skipped.append({
                    "Manager": current_manager_id,
                    "target": network_uri,
                    "reason": "NTP block is not available",
                })
                continue
            plan.append({
                "Manager": current_manager_id,
                "target": network_uri,
                "payload": payload,
            })

        if manager_id and not plan:
            raise InvalidArgument(f"Manager {manager_id!r} has no NTP-capable NetworkProtocol")
        if not plan:
            raise InvalidArgument("no NTP-capable ManagerNetworkProtocol resources found")
        return plan, skipped

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                servers=None,
                manager_id: Optional[str] = None,
                clear: Optional[bool] = False,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or apply NTP servers on ManagerNetworkProtocol resources.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: run the discovery and PATCH requests asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :param servers: the NTP hostnames or IPs to set (up to four).
        :param manager_id: optional Manager id to restrict the write to.
        :param clear: restore an empty NTP server list instead of setting servers.
        :param confirm: apply the PATCH; without it the command only previews.
        :return: CommandResult whose data is the dry-run preview (dry_run, plan,
            skipped) without ``--confirm``, or the applied results with it.
        :raises InvalidArgument: when the servers are invalid or no NTP-capable
            resource is found (via the normalize/plan helpers).
        """
        normalized_servers = _normalize_ntp_servers(servers, bool(clear))
        plan, skipped = self._ntp_plan(normalized_servers, manager_id, do_async)

        if not confirm:
            return CommandResult({
                "dry_run": True,
                "note": "preview only; re-run with --confirm to apply",
                "servers": normalized_servers,
                "plan": plan,
                "skipped": skipped,
            }, None, None, None)

        applied = []
        for item in plan:
            result, status = self.base_patch(
                item["target"],
                payload=item["payload"],
                do_async=do_async,
                expected_status=200,
            )
            applied.append({
                "Manager": item["Manager"],
                "target": item["target"],
                "status": str(status),
                "error": result.error,
            })

        return CommandResult({
            "servers": normalized_servers,
            "applied": applied,
            "skipped": skipped,
        }, None, None, None)
