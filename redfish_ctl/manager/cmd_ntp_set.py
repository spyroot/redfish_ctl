"""Set ManagerNetworkProtocol NTP servers with a guarded PATCH."""

import ipaddress
import re
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_MAX_NTP_SERVERS = 4
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _normalize_ntp_servers(servers, clear: bool = False) -> list[str]:
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


class NtpSet(IDracManager,
             scm_type=ApiRequestType.NtpSet,
             name='ntp-set',
             metaclass=Singleton):
    """Set ManagerNetworkProtocol NTP servers after dry-run preview."""

    def __init__(self, *args, **kwargs):
        super(NtpSet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ntp-set subcommand."""
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
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _get(self, uri, do_async):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _ntp_plan(self, servers, manager_id, do_async):
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
        """Preview or apply NTP servers on ManagerNetworkProtocol resources."""
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
