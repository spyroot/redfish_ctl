"""Inventory vendor OEM extensions across the main resources (vendor-neutral).

    redfish_ctl oem-info

Walks every ComputerSystem, Manager, and Chassis, reads each resource's ``Oem``
block, and reports one row per vendor extension: {Resource, Vendor, Type, Keys}.
This surfaces Dell (``Oem.Dell``), HPE (``Oem.Hpe``), and NVIDIA/OpenBMC
(``Oem.Nvidia`` / ``Oem.OpenBmc``) extensions the same way — so OEM data is
discoverable regardless of vendor, not just for the one with bespoke commands.

Read-only; navigation is by link/``@odata.id`` with no hardcoded ids.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class OemInfo(IDracManager,
             scm_type=ApiRequestType.OemInfo,
             name='oem-info',
             metaclass=Singleton):
    """Inventory the vendor OEM extensions exposed on systems/managers/chassis."""

    def __init__(self, *args, **kwargs):
        super(OemInfo, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``oem-info`` subcommand (read-only)."""
        cmd_parser = cls.base_parser()
        help_text = "command inventory vendor OEM extensions (Dell/HPE/NVIDIA/OpenBMC)"
        return cmd_parser, "oem-info", help_text

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

    def _roots(self, do_async):
        """Every ComputerSystem + Manager + Chassis URI, multi-member aware."""
        roots = []
        for finder in (self.discover_computer_system_ids, self.discover_manager_ids):
            try:
                roots.extend(finder() or [])
            except Exception:
                continue
        roots.extend(self._members(self._get(IDRAC_API.Chassis, do_async)))
        return roots

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Report each resource's OEM vendor extensions and their top-level keys."""
        rows = []
        for root_uri in self._roots(do_async):
            oem = self._get(root_uri, do_async).get("Oem")
            if not isinstance(oem, dict):
                continue
            for vendor, block in oem.items():
                if not isinstance(block, dict):
                    continue
                keys = [k for k in block if not k.startswith("@")]
                rows.append({
                    "Resource": root_uri.rsplit("/", 1)[-1],
                    "Vendor": vendor,
                    "Type": block.get("@odata.type"),
                    "Keys": keys[:25],
                })
        return CommandResult(rows, None, None, None)
