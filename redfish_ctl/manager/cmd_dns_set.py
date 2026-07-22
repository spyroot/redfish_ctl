"""Set Manager EthernetInterface static DNS name servers with a guarded PATCH.

    redfish_ctl dns-set --interface eth0 --server 8.8.8.8 --confirm
    redfish_ctl dns-set --clear --confirm

Author Mus spyroot@gmail.com
"""

from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_MAX_DNS_SERVERS = 4


def _normalize_dns_servers(servers, clear: bool = False) -> list[str]:
    """Normalize DNS server arguments into a clean list.

    The values are deliberately NOT IP-validated on the client: the BMC is the
    authority on address validity, so a bad address surfaces as a real BMC HTTP
    error recorded on the operation span (an APM error trace) rather than being
    masked by a client-side rejection.

    :param servers: one server string, a comma-joined string, or a list of them.
    :param clear: request an empty DNS list; rejects any provided ``servers``.
    :return: the normalized list of DNS servers (empty when ``clear`` is set).
    :raises InvalidArgument: when ``clear`` is combined with servers, no server is
        given, or more than four are given.
    """
    if clear:
        if servers:
            raise InvalidArgument("--clear cannot be used with --server")
        return []
    if servers is None:
        raise InvalidArgument("at least one DNS server is required")
    raw_servers = [servers] if isinstance(servers, str) else list(servers)
    normalized = []
    for raw in raw_servers:
        for value in str(raw).split(","):
            server = value.strip()
            if server:
                normalized.append(server)
    if not normalized:
        raise InvalidArgument("at least one DNS server is required")
    if len(normalized) > _MAX_DNS_SERVERS:
        raise InvalidArgument(
            f"StaticNameServers supports at most {_MAX_DNS_SERVERS} servers")
    return normalized


class DnsSet(RedfishManagerBase,
             scm_type=ApiRequestType.DnsSet,
             name='dns-set',
             metaclass=Singleton):
    """Set Manager EthernetInterface StaticNameServers after a dry-run preview."""

    def __init__(self, *args, **kwargs):
        """Initialize the dns-set command."""
        super(DnsSet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded dns-set subcommand.

        :param cls: the command class the base parser is built from.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--server', action='append', dest='servers', metavar='IP',
            help="DNS server IP; repeat for up to 4 servers")
        cmd_parser.add_argument(
            '--clear', action='store_true', dest='clear', default=False,
            help="clear the static DNS server list")
        cmd_parser.add_argument(
            '--interface', type=str, dest='interface_id', default=None, metavar='ID',
            help="optional Manager EthernetInterface id to patch; default patches all")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm', default=False,
            help="apply the PATCH; without it the command only previews")
        help_text = "set Manager EthernetInterface static DNS servers"
        return cmd_parser, "dns-set", help_text

    def _interface_uris(self, do_async: bool) -> list[str]:
        """Discover the Manager EthernetInterface member URIs.

        :param do_async: run the discovery queries asynchronously.
        :return: list of EthernetInterface ``@odata.id`` URIs across managers.
        """
        uris: list[str] = []
        for manager_uri in self.discover_manager_ids():
            try:
                manager = self.base_query(manager_uri, do_async=do_async).data or {}
            except Exception:
                continue
            collection = (manager.get("EthernetInterfaces") or {}).get("@odata.id")
            if not collection:
                continue
            try:
                members = (self.base_query(
                    collection, do_async=do_async).data or {}).get("Members", [])
            except Exception:
                continue
            uris.extend(m["@odata.id"] for m in members if "@odata.id" in m)
        return uris

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                servers=None,
                interface_id: Optional[str] = None,
                clear: Optional[bool] = False,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or apply static DNS servers on Manager EthernetInterfaces.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: run the discovery and PATCH requests asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :param servers: the DNS server IPs to set (up to four).
        :param interface_id: optional Manager EthernetInterface id to restrict the write to.
        :param clear: clear the static DNS server list instead of setting servers.
        :param confirm: apply the PATCH; without it the command only previews.
        :return: CommandResult whose data is the dry-run preview (dry_run, servers,
            targets) without ``--confirm``, or the applied results with it.
        :raises InvalidArgument: when the servers are invalid or no Manager
            EthernetInterface is found.
        """
        normalized = _normalize_dns_servers(servers, bool(clear))
        payload = {"StaticNameServers": normalized}

        targets = self._interface_uris(bool(do_async))
        if interface_id:
            targets = [
                uri for uri in targets
                if uri.rstrip("/").rsplit("/", 1)[-1] == interface_id
            ]
        if not targets:
            raise InvalidArgument("no Manager EthernetInterface resources found")

        if not confirm:
            return CommandResult({
                "dry_run": True,
                "note": "preview only; re-run with --confirm to apply",
                "servers": normalized,
                "targets": targets,
            }, None, None, None)

        applied = []
        for uri in targets:
            result, status = self.base_patch(
                uri, payload=payload, do_async=do_async, expected_status=200)
            applied.append({
                "target": uri,
                "status": str(status),
                "error": result.error,
            })

        return CommandResult({
            "servers": normalized,
            "applied": applied,
        }, None, None, None)
