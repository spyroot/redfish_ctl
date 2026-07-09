"""Read GPU NVLink ports + per-port metrics out-of-band.

    redfish_ctl nvlink-ports

Walks ``/redfish/v1/Systems`` -> each System ``Processors`` -> each GPU
Processor -> its ``Ports`` -> each NVLink Port -> the Port ``Metrics`` leaf,
returning one row per port {System, GPU, Port, LinkState, LinkStatus,
CurrentSpeedGbps, MaxSpeedGbps, RXBytes, TXBytes, BitErrorRate}.

This is the out-of-band NVLink fabric view: link state, negotiated speed, and
traffic/error counters per GPU port, read straight from the BMC with no host OS
or driver. The walk filters on ``ProcessorType == GPU`` and
``PortProtocol == NVLink`` and follows links only (no hardcoded ids), and it
tolerates GPUs/ports whose Metrics leaf was not exposed (counters come back
None) rather than failing the whole walk.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class NvLinkPorts(IDracManager,
                  scm_type=ApiRequestType.NvLinkPorts,
                  name='nvlink-ports',
                  metaclass=Singleton):
    """Read every GPU NVLink port and its traffic/error metrics."""

    def __init__(self, *args, **kwargs):
        super(NvLinkPorts, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``nvlink-ports`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command read GPU NVLink ports and per-port metrics"
        return cmd_parser, "nvlink-ports", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly."""
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _link(self, data, key, do_async):
        """Follow a single ``{key: {@odata.id}}`` link, returning its body or {}."""
        link = (data or {}).get(key)
        uri = link.get("@odata.id") if isinstance(link, dict) else None
        if not uri:
            return {}
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
        """Walk Systems -> GPU Processors -> NVLink Ports -> Metrics.

        On a multi-system host (e.g. a host System plus an HGX baseboard System)
        the GPUs live under whichever System exposes them; both are walked. A
        port whose Metrics leaf is absent still yields a row with None counters.
        """
        rows = []
        try:
            systems = self.base_query(f"{RedfishApi.Version}/Systems",
                                      do_async=do_async).data or {}
        except Exception:
            return CommandResult(rows, None, None, None)

        for system_uri in self._members(systems):
            try:
                sdata = self.base_query(system_uri, do_async=do_async).data or {}
            except Exception:
                continue
            procs = self._link(sdata, "Processors", do_async)
            for proc_uri in self._members(procs):
                try:
                    proc = self.base_query(proc_uri, do_async=do_async).data or {}
                except Exception:
                    continue
                if proc.get("ProcessorType") != "GPU":
                    continue
                gpu_id = proc.get("Id") or proc_uri.rsplit("/", 1)[-1]
                ports = self._link(proc, "Ports", do_async)
                for port_uri in self._members(ports):
                    try:
                        port = self.base_query(port_uri, do_async=do_async).data or {}
                    except Exception:
                        continue
                    if port.get("PortProtocol") != "NVLink":
                        continue
                    metrics = self._link(port, "Metrics", do_async)
                    oem = (metrics.get("Oem") or {}).get("Nvidia") or {}
                    rows.append({
                        "System": system_uri.rsplit("/", 1)[-1],
                        "GPU": gpu_id,
                        "Port": port.get("Id") or port_uri.rsplit("/", 1)[-1],
                        "LinkState": port.get("LinkState"),
                        "LinkStatus": port.get("LinkStatus"),
                        "CurrentSpeedGbps": port.get("CurrentSpeedGbps"),
                        "MaxSpeedGbps": port.get("MaxSpeedGbps"),
                        "RXBytes": metrics.get("RXBytes"),
                        "TXBytes": metrics.get("TXBytes"),
                        "BitErrorRate": oem.get("BitErrorRate"),
                    })
        return CommandResult(rows, None, None, None)
