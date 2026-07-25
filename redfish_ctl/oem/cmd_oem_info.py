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
from ..idrac_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class OemInfo(IDracManager,
             scm_type=ApiRequestType.OemInfo,
             name='oem-info',
             metaclass=Singleton):
    """Inventory the vendor OEM extensions exposed on systems/managers/chassis."""

    def __init__(self, *args, **kwargs):
        """Initialize the oem-info command."""
        super(OemInfo, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``oem-info`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command inventory vendor OEM extensions (Dell/HPE/NVIDIA/OpenBMC)"
        return cmd_parser, "oem-info", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (or any value; non-dicts yield []).
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async event loop when True.
        :return: the parsed response body, or {} when the query fails.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _roots(self, do_async):
        """Every ComputerSystem + Manager + Chassis URI, multi-member aware.

        :param do_async: issue the Chassis query on the async event loop when True.
        :return: list of root resource URIs to inspect for OEM extensions.
        """
        roots = []
        for finder in (self.discover_computer_system_ids, self.discover_manager_ids):
            try:
                roots.extend(finder() or [])
            except Exception:
                continue
        roots.extend(self._members(self._get(REDFISH_API.Chassis, do_async)))
        return roots

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Report each resource's OEM vendor extensions and their top-level keys.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries on the async event loop when True.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult whose data is a list of OEM rows
            {Resource, Vendor, Type, Keys}.
        """
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
