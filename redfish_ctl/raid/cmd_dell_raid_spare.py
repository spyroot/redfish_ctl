"""Assign or unassign Dell RAID hot spare disks.

    redfish_ctl dell-raid-spare
    redfish_ctl dell-raid-spare --action assign --disk-fqdd Disk.Bay.4:...
    redfish_ctl dell-raid-spare --action assign --disk-fqdd Disk.Bay.4:... --virtual-disk Disk.Virtual.0:...
    redfish_ctl dell-raid-spare --action unassign --disk-fqdd Disk.Bay.4:... --confirm

``#DellRaidService.AssignSpare`` and ``#DellRaidService.UnassignSpare`` change
storage configuration, so this command previews unless ``--confirm`` is given.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_ASSIGN_SPARE_ACTION = "#DellRaidService.AssignSpare"
_UNASSIGN_SPARE_ACTION = "#DellRaidService.UnassignSpare"
_ACTION_TYPES = {
    "assign": _ASSIGN_SPARE_ACTION,
    "unassign": _UNASSIGN_SPARE_ACTION,
}
_SYSTEM_FALLBACK = f"{RedfishApi.Version}/Systems/System.Embedded.1"
_SERVICE_SUFFIX = "Oem/Dell/DellRaidService"


class DellRaidSpareActions(IDracManager,
                           scm_type=ApiRequestType.DellRaidSpareActions,
                           name="dell-raid-spare",
                           metaclass=Singleton):
    """Assign or unassign DellRaidService hot spare disks."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-spare command."""
        super(DellRaidSpareActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-spare`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            required=False,
            choices=sorted(_ACTION_TYPES),
            default=None,
            help="spare action to preview or invoke: assign or unassign",
        )
        cmd_parser.add_argument(
            "--disk-fqdd",
            required=False,
            dest="disk_fqdd",
            type=str,
            default=None,
            help="physical disk FQDD used as the hot spare target",
        )
        cmd_parser.add_argument(
            "--virtual-disk",
            required=False,
            dest="virtual_disk",
            action="append",
            default=None,
            help="virtual disk FQDD for a dedicated hot spare; repeatable",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the DellRaidService spare POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-raid-spare",
            "command assign or unassign Dell RAID hot spare disks",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _oem_dell_link(resource, key):
        """Return a Dell OEM link from ``Links.Oem.Dell`` or ``Oem.Dell``.

        :param resource: Redfish resource body to inspect.
        :param key: Dell OEM link name.
        :return: the linked URI, or None when absent.
        """
        links = (resource or {}).get("Links") or {}
        link = DellRaidSpareActions._link(
            ((links.get("Oem") or {}).get("Dell") or {}),
            key,
        )
        if link:
            return link
        return DellRaidSpareActions._link(
            (((resource or {}).get("Oem") or {}).get("Dell") or {}),
            key,
        )

    def _system_uri(self):
        """Return the selected ComputerSystem URI.

        :return: the manager-selected system URI, or the Dell default fallback.
        """
        try:
            system_uri = self.idrac_manage_servers
        except Exception:
            system_uri = ""
        return system_uri or _SYSTEM_FALLBACK

    def _read_optional(self, uri, do_async):
        """Read a Redfish resource, returning ``None`` on lookup failure.

        :param uri: Redfish resource URI.
        :param do_async: issue the query over the async Redfish path.
        :return: resource body when readable, otherwise None.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return None

    def _raid_service(self, do_async):
        """Resolve and read the DellRaidService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` for the first readable candidate.
        """
        system_uri = self._system_uri()
        system = self._read_optional(system_uri, do_async) or {}
        system_id = system_uri.rsplit("/", 1)[-1]
        candidates = [
            self._oem_dell_link(system, "DellRaidService"),
            f"{system_uri}/{_SERVICE_SUFFIX}",
            f"/redfish/v1/Dell/Systems/{system_id}/DellRaidService",
        ]
        seen = set()
        for uri in candidates:
            if not uri or uri in seen:
                continue
            seen.add(uri)
            data = self._read_optional(uri, do_async)
            if data is not None:
                return uri, data
        return (
            candidates[1],
            CommandResult(
                None,
                None,
                None,
                "DellRaidService is not available on this host",
            ),
        )

    def _storage_candidates(self, do_async):
        """Collect Dell storage drive and volume candidate identifiers.

        :param do_async: issue queries over the async Redfish path.
        :return: dict with ``drives`` and ``virtual_disks`` candidate lists.
        """
        system_uri = self._system_uri()
        system = self._read_optional(system_uri, do_async) or {}
        storage_uri = self._link(system, "Storage") or f"{system_uri}/Storage"
        storage_collection = self._read_optional(storage_uri, do_async) or {}
        drives = []
        virtual_disks = []
        for member in storage_collection.get("Members") or []:
            storage_member_uri = self._link({"member": member}, "member")
            if not storage_member_uri:
                continue
            storage = self._read_optional(storage_member_uri, do_async) or {}
            for drive_link in storage.get("Drives") or []:
                drive_uri = self._link({"drive": drive_link}, "drive")
                drive = self._read_optional(drive_uri, do_async) or {}
                drives.append({
                    "id": drive.get("Id") or (drive_uri or "").rsplit("/", 1)[-1],
                    "uri": drive_uri,
                    "hotspare_type": drive.get("HotspareType"),
                    "raid_status": (
                        (((drive.get("Oem") or {}).get("Dell") or {})
                         .get("DellPhysicalDisk") or {})
                        .get("RaidStatus")
                    ),
                })
            volume_collection_uri = self._link(storage, "Volumes")
            volume_collection = (
                self._read_optional(volume_collection_uri, do_async)
                if volume_collection_uri else {}
            ) or {}
            for volume_link in volume_collection.get("Members") or []:
                volume_uri = self._link({"volume": volume_link}, "volume")
                volume = self._read_optional(volume_uri, do_async) or {}
                virtual_disks.append({
                    "id": volume.get("Id") or (volume_uri or "").rsplit("/", 1)[-1],
                    "uri": volume_uri,
                    "name": volume.get("Name"),
                    "volume_type": volume.get("VolumeType"),
                })
        return {"drives": drives, "virtual_disks": virtual_disks}

    @staticmethod
    def _actions_summary(service):
        """Return discovered DellRaidService spare action targets.

        :param service: DellRaidService resource body.
        :return: mapping keyed by ``assign`` and ``unassign`` when advertised.
        """
        targets = IDracManager._flatten_action_targets(service)
        return {
            label: {"action": full, "target": targets[full]}
            for label, full in _ACTION_TYPES.items()
            if full in targets
        }

    def _metadata(self, do_async):
        """Return spare-action target metadata without mutating.

        :param do_async: issue queries over the async Redfish path.
        :return: CommandResult with target and storage candidate metadata.
        """
        raid_uri, service = self._raid_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        return CommandResult(
            {
                "raid_service": raid_uri,
                "actions": self._actions_summary(service),
                "candidates": self._storage_candidates(do_async),
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _clean_required(value, label):
        """Normalize a required string option.

        :param value: option value to normalize.
        :param label: user-facing field label for errors.
        :return: stripped string.
        :raises InvalidArgument: when the value is missing or empty.
        """
        stripped = (value or "").strip()
        if not stripped:
            raise InvalidArgument(f"{label} cannot be empty")
        return stripped

    @staticmethod
    def _clean_virtual_disks(values):
        """Normalize optional dedicated-hot-spare virtual disk values.

        :param values: iterable of CLI-provided virtual disk identifiers.
        :return: list of non-empty identifiers.
        """
        return [item.strip() for item in (values or []) if item and item.strip()]

    @classmethod
    def _payload(cls, action, disk_fqdd, virtual_disks=None):
        """Build the DellRaidService spare action payload.

        :param action: ``assign`` or ``unassign``.
        :param disk_fqdd: physical disk FQDD.
        :param virtual_disks: optional dedicated-hot-spare virtual disk FQDDs.
        :return: JSON-serializable DellRaidService payload.
        :raises InvalidArgument: when required values are missing or mismatched.
        """
        disk = cls._clean_required(disk_fqdd, "disk FQDD")
        payload = {"TargetFQDD": disk}
        vds = cls._clean_virtual_disks(virtual_disks)
        if action == "assign" and vds:
            payload["VirtualDiskArray"] = vds
        elif action == "unassign" and vds:
            raise InvalidArgument("--virtual-disk is only valid with --action assign")
        return payload

    def execute(self,
                action: Optional[str] = None,
                disk_fqdd: Optional[str] = None,
                virtual_disk: Optional[list[str]] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List spare targets, preview a spare action, or invoke it.

        With no ``--action`` and no ``--disk-fqdd``, the command lists the
        advertised spare action targets plus candidate drives and virtual disks.
        Otherwise it builds the Dell spare payload and delegates the POST guard
        to ``invoke_action``; the action only fires with ``--confirm``.

        :param action: ``assign`` or ``unassign``.
        :param disk_fqdd: physical disk FQDD to assign or unassign.
        :param virtual_disk: optional virtual disk FQDDs for a dedicated hot spare.
        :param confirm: authorize the DellRaidService POST to actually fire.
        :param dry_run: resolve the target without POSTing; overrides ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with metadata, a dry-run preview, or POST result.
        """
        if action is None and disk_fqdd is None:
            return self._metadata(do_async)
        selected = self._clean_required(action, "action")
        if selected not in _ACTION_TYPES:
            raise InvalidArgument("action must be one of: assign, unassign")
        raid_uri, service = self._raid_service(do_async)
        if isinstance(service, CommandResult):
            return service
        short_action = _ACTION_TYPES[selected].rsplit(".", 1)[-1]
        return self.invoke_action(
            raid_uri,
            short_action,
            payload=self._payload(selected, disk_fqdd, virtual_disk),
            full_action_type=_ACTION_TYPES[selected],
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
