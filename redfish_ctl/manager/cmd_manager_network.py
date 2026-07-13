"""Read ManagerNetworkProtocol settings for every Redfish Manager."""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class ManagerNetworkProtocol(CommandBase,
                             scm_type=ApiRequestType.ManagerNetworkProtocol,
                             name='manager-network',
                             metaclass=Singleton):
    """Read BMC network protocol enablement and NTP settings."""

    def __init__(self, *args, **kwargs):
        super(ManagerNetworkProtocol, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only manager-network subcommand."""
        cmd_parser = cls.base_parser()
        help_text = "command read ManagerNetworkProtocol service state"
        return cmd_parser, "manager-network", help_text

    @staticmethod
    def _link(data, key):
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _protocol(data, key):
        protocol = (data or {}).get(key)
        if not isinstance(protocol, dict):
            return {"ProtocolEnabled": None, "Port": None}
        return {
            "ProtocolEnabled": protocol.get("ProtocolEnabled"),
            "Port": protocol.get("Port"),
        }

    @staticmethod
    def _ntp(data):
        ntp = (data or {}).get("NTP")
        if not isinstance(ntp, dict):
            return {"ProtocolEnabled": None, "NTPServers": []}
        servers = ntp.get("NTPServers")
        if not isinstance(servers, list):
            servers = []
        return {
            "ProtocolEnabled": ntp.get("ProtocolEnabled"),
            "NTPServers": servers,
        }

    def _get(self, uri, do_async):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read ManagerNetworkProtocol rows from every manager."""
        rows = []
        for manager_uri in self.discover_manager_ids():
            manager = self._get(manager_uri, do_async)
            network_uri = self._link(manager, "NetworkProtocol")
            if not network_uri:
                continue
            network = self._get(network_uri, do_async)
            if not network:
                continue
            status = network.get("Status") or {}
            manager_id = manager.get("Id")
            if not manager_id and "/Managers/" in network_uri:
                manager_id = network_uri.rstrip("/").split("/")[-2]
            if not manager_id:
                manager_id = manager_uri.rsplit("/", 1)[-1]
            rows.append({
                "Manager": manager_id,
                "HostName": network.get("HostName"),
                "FQDN": network.get("FQDN"),
                "HTTP": self._protocol(network, "HTTP"),
                "HTTPS": self._protocol(network, "HTTPS"),
                "IPMI": self._protocol(network, "IPMI"),
                "SSH": self._protocol(network, "SSH"),
                "NTP": self._ntp(network),
                "Health": status.get("Health") if isinstance(status, dict) else None,
                "State": status.get("State") if isinstance(status, dict) else None,
            })
        return CommandResult(rows, None, None, None)
