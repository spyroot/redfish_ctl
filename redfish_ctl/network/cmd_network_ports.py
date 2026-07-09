"""Read per-adapter network Port link state across all Chassis.

    idrac_ctl network-ports

Walks ``/redfish/v1/Chassis`` -> each chassis ``NetworkAdapters`` -> each adapter
-> its ``Ports`` collection, returning {Chassis, Adapter, Port, LinkStatus,
LinkState, LinkNetworkTechnology, CurrentSpeedGbps, MaxSpeedGbps}. This is the
per-port link view (is the NIC/fabric port up, at what speed) that the
``network-adapters`` hardware inventory stops short of.

Navigation is by link/``@odata.id`` with no hardcoded ids; an adapter without a
Ports link is skipped. Works on Dell, HPE iLO, Supermicro, etc.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class NetworkPorts(IDracManager,
                   scm_type=ApiRequestType.NetworkPorts,
                   name='network-ports',
                   metaclass=Singleton):
    """Read every NetworkAdapter Port's link state across all chassis."""

    def __init__(self, *args, **kwargs):
        super(NetworkPorts, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``network-ports`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command read NetworkAdapter port link state (up/speed) per chassis"
        return cmd_parser, "network-ports", help_text

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

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk every chassis adapter's Ports and collect per-port link state."""
        rows = []
        chassis = self.base_query(IDRAC_API.Chassis, do_async=do_async)
        for chassis_uri in self._members(chassis.data):
            adapters_uri = self._link(self._get(chassis_uri, do_async), "NetworkAdapters")
            if not adapters_uri:
                continue
            for adapter_uri in self._members(self._get(adapters_uri, do_async)):
                adapter = self._get(adapter_uri, do_async)
                ports_uri = self._link(adapter, "Ports")
                if not ports_uri:
                    continue
                for port_uri in self._members(self._get(ports_uri, do_async)):
                    port = self._get(port_uri, do_async)
                    if not isinstance(port, dict):
                        continue
                    rows.append({
                        "Chassis": chassis_uri.rsplit("/", 1)[-1],
                        "Adapter": adapter.get("Id") or adapter_uri.rsplit("/", 1)[-1],
                        "Port": port.get("Id") or port_uri.rsplit("/", 1)[-1],
                        "LinkStatus": port.get("LinkStatus"),
                        "LinkState": port.get("LinkState"),
                        "LinkNetworkTechnology": port.get("LinkNetworkTechnology"),
                        "CurrentSpeedGbps": port.get("CurrentSpeedGbps"),
                        "MaxSpeedGbps": port.get("MaxSpeedGbps"),
                    })
        return CommandResult(rows, None, None, None)
