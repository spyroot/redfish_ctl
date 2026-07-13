"""Read Redfish NetworkAdapters across all Chassis (NICs + DPUs).

    redfish_ctl network-adapters

Walks ``/redfish/v1/Chassis`` -> each chassis ``NetworkAdapters`` collection ->
each adapter, returning {Chassis, Id, Model, Manufacturer, DeviceClass,
SerialNumber, PartNumber, Health}. ``DeviceClass`` is inferred from the model
string (BlueField -> DPU, ConnectX -> NIC) so a SmartNIC/DPU is distinguishable
from a plain NIC.

Navigation is by link/``@odata.id`` with no hardcoded ids — a chassis without a
NetworkAdapters link is skipped — so it works on any host exposing the modern
Redfish NetworkAdapter model.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, RedfishEndpoint, Singleton
from ..redfish_manager import CommandResult


class NetworkAdapters(CommandBase,
                      scm_type=ApiRequestType.NetworkAdapters,
                      name='network-adapters',
                      metaclass=Singleton):
    """Read every NetworkAdapter (NIC/DPU) across all chassis."""

    def __init__(self, *args, **kwargs):
        super(NetworkAdapters, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``network-adapters`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command read all chassis NetworkAdapters (NICs and DPUs)"
        return cmd_parser, "network-adapters", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly."""
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    @staticmethod
    def _device_class(model):
        """Classify an adapter as DPU vs NIC from its model string.

        BlueField is a DPU/SmartNIC; ConnectX is a NIC. Unknown models fall
        through to NIC, which is the safe default for an Ethernet adapter.
        """
        m = (model or "").lower()
        if "bluefield" in m or "bf3" in m or "bf2" in m:
            return "DPU"
        if "connectx" in m or "cx8" in m or "cx7" in m:
            return "NIC"
        return "NIC"

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk every chassis NetworkAdapters collection and collect adapters.

        Tolerant of a chassis with no NetworkAdapters link or an unreachable
        collection (skips it).
        """
        rows = []
        chassis = self.base_query(RedfishEndpoint.Chassis, do_async=do_async)
        for chassis_uri in self._members(chassis.data):
            try:
                cdata = self.base_query(chassis_uri, do_async=do_async).data or {}
            except Exception:
                continue
            link = cdata.get("NetworkAdapters")
            adapters_uri = link.get("@odata.id") if isinstance(link, dict) else None
            if not adapters_uri:
                continue
            try:
                coll = self.base_query(adapters_uri, do_async=do_async,
                                       do_expanded=do_expanded).data or {}
            except Exception:
                continue
            for adapter_uri in self._members(coll):
                try:
                    ad = self.base_query(adapter_uri, do_async=do_async).data or {}
                except Exception:
                    continue
                status = ad.get("Status") or {}
                rows.append({
                    "Chassis": chassis_uri.rsplit("/", 1)[-1],
                    "Id": ad.get("Id") or adapter_uri.rsplit("/", 1)[-1],
                    "Model": ad.get("Model"),
                    "Manufacturer": ad.get("Manufacturer"),
                    "DeviceClass": self._device_class(ad.get("Model")),
                    "SerialNumber": ad.get("SerialNumber"),
                    "PartNumber": ad.get("PartNumber"),
                    "Health": status.get("Health") if isinstance(status, dict) else None,
                })
        return CommandResult(rows, None, None, None)
