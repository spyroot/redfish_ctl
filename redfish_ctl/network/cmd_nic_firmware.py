"""Read network-adapter (NIC/DPU) firmware across all chassis — OOB, vendor-neutral.

    redfish_ctl nic-firmware

Joins two read-only Redfish views into one NIC-firmware report:

  * ``Chassis/*/NetworkAdapters/*``     -> physical NIC/DPU cards (model, health)
  * ``UpdateService/FirmwareInventory`` -> component firmware versions

Firmware entries are matched to network devices by an id/name token heuristic
(ConnectX/CX*, BlueField/BF*, NIC, Mellanox) because a GB300 FirmwareInventory
leaves ``RelatedItem`` empty, so there is no explicit link back to the adapter.
Only the network-relevant firmware members are fetched for their ``Version``, so
the walk stays cheap and never GETs unrelated BMC/GPU/PCIe firmware.

Navigation is by ``@odata.id`` link with no vendor-specific ids, so it works on
any host exposing the modern Redfish NetworkAdapter + FirmwareInventory model. A
chassis with no NetworkAdapters link, or a host with no FirmwareInventory, is
tolerated (that slice comes back empty rather than raising).

Author Mus spyroot@gmail.com
"""
import re
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_shared import RedfishApi
from .cmd_network_adapters import NetworkAdapters

# The package does ``from .network.cmd_nic_firmware import *``; keep that to the
# command class so re-exported imports (CommandResult, RedfishManagerBase, enums)
# do not leak into the top-level ``redfish_ctl`` namespace. ``network_class`` stays
# importable directly (``from ...cmd_nic_firmware import network_class``).
__all__ = ["NicFirmware"]

# Tokens that mark a firmware component (or adapter model) as a network device.
# Matched against alphanumeric tokens (not raw substrings) so a GUID/version that
# merely contains "cx8"/"nic" as a fragment is not misclassified.
_DPU_TOKENS = frozenset({"bluefield", "bf3", "bf2"})
_NIC_TOKENS = frozenset({"connectx", "cx8", "cx7", "cx6", "mellanox", "mlnx", "nic"})
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Leading FirmwareInventory prefixes that duplicate one component (Dell exposes a
# Current-* and Installed-* member per device); stripped to collapse the pair.
_FW_STATE_PREFIX_RE = re.compile(r"^(current|installed|previous|available|rollback)-", re.I)


def network_class(text: Optional[str]) -> Optional[str]:
    """Classify a firmware id/name as ``DPU``, ``NIC``, or ``None`` (not network).

    BlueField is a DPU/SmartNIC; ConnectX/Mellanox is a NIC; a bare ``nic`` token is
    the weak last signal. Matching is on whole alphanumeric tokens, so BMC/GPU/PCIe
    firmware and GUIDs (which may contain a fragment like ``cx8``) return None.

    :param text: a firmware id or name to classify (may be None).
    :return: "DPU", "NIC", or None when the text is not a network device.
    """
    tokens = set(_TOKEN_RE.findall((text or "").lower()))
    if tokens & _DPU_TOKENS:
        return "DPU"
    if tokens & _NIC_TOKENS:
        return "NIC"
    return None


class NicFirmware(RedfishManagerBase,
                  scm_type=ApiRequestType.NicFirmware,
                  name='nic-firmware',
                  metaclass=Singleton):
    """Read every NIC/DPU adapter and its firmware version across all chassis."""

    def __init__(self, *args, **kwargs):
        """Initialize the nic-firmware command."""
        super(NicFirmware, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``nic-firmware`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read NIC/DPU network-adapter firmware (out-of-band)"
        return cmd_parser, "nic-firmware", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: a Redfish collection body (expects a ``Members`` list).
        :return: list of member ``@odata.id`` strings ([] if data is not a dict).
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def _adapters(self, do_async):
        """Physical NIC/DPU cards from ``Chassis/*/NetworkAdapters/*``.

        :param do_async: note async will subscribe to an event loop.
        :return: list of adapter dicts (empty if the Chassis walk is unavailable).
        """
        rows = []
        try:
            chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        except Exception:
            # A transient error / 403 on the Chassis collection must not sink the
            # whole command — the firmware slice (fetched separately) can still land.
            return rows
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
                coll = self.base_query(adapters_uri, do_async=do_async).data or {}
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
                    "DeviceClass": NetworkAdapters._device_class(ad.get("Model")),
                    "PartNumber": ad.get("PartNumber"),
                    "SerialNumber": ad.get("SerialNumber"),
                    "Health": status.get("Health") if isinstance(status, dict) else None,
                })
        return rows

    def _nic_firmware(self, do_async):
        """NIC/DPU firmware versions from ``UpdateService/FirmwareInventory``.

        Fetches the collection once, then follows only the network-relevant member
        links (identified by id/name token) to read each ``Version``. When the BMC
        honours ``$expand`` and returns members inline with a Version, that value is
        used without a second GET.

        :param do_async: note async will subscribe to an event loop.
        :return: list of network-relevant firmware component dicts (empty if unavailable).
        """
        rows = []
        fw_root = f"{RedfishApi.Version}/UpdateService/FirmwareInventory"
        try:
            coll = self.base_query(fw_root, do_async=do_async).data or {}
        except Exception:
            return rows
        seen_components: set[str] = set()
        for member in coll.get("Members", []):
            if not isinstance(member, dict):
                continue
            uri = member.get("@odata.id")
            if not isinstance(uri, str):
                continue
            fw_id = uri.rsplit("/", 1)[-1]
            device_class = network_class(fw_id) or network_class(member.get("Name"))
            if device_class is None:
                continue
            # Collapse Dell-style Current-*/Installed-* pairs to one component so the
            # count is not doubled and the duplicate is not re-fetched.
            base_id = _FW_STATE_PREFIX_RE.sub("", fw_id).lower()
            if base_id in seen_components:
                continue
            seen_components.add(base_id)
            entry = member if "Version" in member else None
            if entry is None:
                try:
                    entry = self.base_query(uri, do_async=do_async).data or {}
                except Exception:
                    entry = {}
            rows.append({
                "Id": entry.get("Id") or fw_id,
                "Name": entry.get("Name"),
                "Version": entry.get("Version"),
                "Updateable": entry.get("Updateable"),
                "Manufacturer": entry.get("Manufacturer"),
                "DeviceClass": network_class(entry.get("Name")) or device_class,
                "Uri": uri,
            })
        return rows

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Return the joined NIC/DPU adapter + firmware report (read-only).

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult holding {"adapters", "firmware", "summary"}.
        """
        adapters = self._adapters(do_async)
        firmware = self._nic_firmware(do_async)
        versions = sorted({f["Version"] for f in firmware if f.get("Version")})
        summary = {
            "adapter_count": len(adapters),
            "nic_count": sum(1 for a in adapters if a.get("DeviceClass") == "NIC"),
            "dpu_count": sum(1 for a in adapters if a.get("DeviceClass") == "DPU"),
            "firmware_count": len(firmware),
            "updateable_count": sum(1 for f in firmware if f.get("Updateable")),
            "distinct_versions": versions,
        }
        payload = {"adapters": adapters, "firmware": firmware, "summary": summary}
        return CommandResult(payload, None, None, None)
