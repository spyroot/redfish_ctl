"""Read Redfish EthernetInterfaces (host + BMC NIC IP/MAC/VLAN config).

    redfish_ctl ethernet-interfaces

Walks every ComputerSystem and Manager, follows their ``EthernetInterfaces``
collection, and returns {Source, Id, Name, MACAddress, LinkStatus, SpeedMbps,
IPv4}. This is the actual network *configuration* (addresses, MACs) — distinct
from ``network-adapters``, which inventories chassis NIC/DPU hardware.

Navigation is by link/``@odata.id`` with no hardcoded ids; a system/manager with
no EthernetInterfaces link is skipped. Works on Dell, HPE iLO, Supermicro, etc.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class EthernetInterfaces(CommandBase,
                         scm_type=ApiRequestType.EthernetInterfaces,
                         name='ethernet-interfaces',
                         metaclass=Singleton):
    """Read EthernetInterface config from every system and manager."""

    def __init__(self, *args, **kwargs):
        super(EthernetInterfaces, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``ethernet-interfaces`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command read host and BMC EthernetInterfaces (IP/MAC/VLAN)"
        return cmd_parser, "ethernet-interfaces", help_text

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

    @staticmethod
    def _ipv4(data):
        """First IPv4 address string on the interface, or None."""
        addrs = (data or {}).get("IPv4Addresses")
        if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict):
            return addrs[0].get("Address")
        return None

    def _roots(self):
        """Every ComputerSystem + Manager URI (multi-member aware), tolerant."""
        roots = []
        for finder in (self.discover_computer_system_ids, self.discover_manager_ids):
            try:
                roots.extend(finder() or [])
            except Exception:
                continue
        return roots

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk EthernetInterfaces on every system/manager and collect config."""
        rows = []
        for root_uri in self._roots():
            rdata = self._get(root_uri, do_async)
            coll_uri = self._link(rdata, "EthernetInterfaces")
            if not coll_uri:
                continue
            for iface_uri in self._members(self._get(coll_uri, do_async)):
                iface = self._get(iface_uri, do_async)
                if not isinstance(iface, dict):
                    continue
                status = iface.get("Status") or {}
                rows.append({
                    "Source": root_uri.rsplit("/", 1)[-1],
                    "Id": iface.get("Id") or iface_uri.rsplit("/", 1)[-1],
                    "Name": iface.get("Name"),
                    "MACAddress": iface.get("MACAddress") or iface.get("PermanentMACAddress"),
                    "LinkStatus": iface.get("LinkStatus"),
                    "SpeedMbps": iface.get("SpeedMbps"),
                    "IPv4": self._ipv4(iface),
                    "Health": status.get("Health") if isinstance(status, dict) else None,
                })
        return CommandResult(rows, None, None, None)
