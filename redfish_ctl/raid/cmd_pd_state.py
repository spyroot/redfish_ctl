"""Preview or run DellRaidService physical disk actions.

    redfish_ctl dell-raid-pd-actions
    redfish_ctl dell-raid-pd-actions --action state --disk-fqdd Disk.Bay.4 --state Online
    redfish_ctl dell-raid-pd-actions --action rebuild --disk-fqdd Disk.Bay.4 --confirm

The command resolves DellRaidService from the selected ComputerSystem and lists
advertised physical-disk actions without mutating by default. ``--confirm`` is
required before any destructive POST is sent.
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_CHANGE_PD_STATE_ACTION = "#DellRaidService.ChangePDState"
_PREPARE_TO_REMOVE_ACTION = "#DellRaidService.PrepareToRemove"
_REBUILD_PHYSICAL_DISK_ACTION = "#DellRaidService.RebuildPhysicalDisk"
_SYSTEM_FALLBACK = f"{RedfishApi.Version}/Systems/System.Embedded.1"
_SERVICE_SUFFIX = "Oem/Dell/DellRaidService"


@dataclass(frozen=True)
class _PhysicalDiskAction:
    """Static selector metadata for one DellRaidService physical-disk action."""

    selector: str
    full_type: str
    action_name: str
    required_payload: tuple[str, ...]


_ACTION_SPECS = {
    "prepare-remove": _PhysicalDiskAction(
        selector="prepare-remove",
        full_type=_PREPARE_TO_REMOVE_ACTION,
        action_name="PrepareToRemove",
        required_payload=("TargetFQDD", "ForceRemove"),
    ),
    "rebuild": _PhysicalDiskAction(
        selector="rebuild",
        full_type=_REBUILD_PHYSICAL_DISK_ACTION,
        action_name="RebuildPhysicalDisk",
        required_payload=("TargetFQDD",),
    ),
    "state": _PhysicalDiskAction(
        selector="state",
        full_type=_CHANGE_PD_STATE_ACTION,
        action_name="ChangePDState",
        required_payload=("TargetFQDD", "State"),
    ),
}


class DellRaidPhysicalDiskActions(
    IDracManager,
    scm_type=ApiRequestType.DellRaidPhysicalDiskActions,
    name="dell-raid-pd-actions",
    metaclass=Singleton,
):
    """Preview or run DellRaidService physical-disk actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-pd-actions command."""
        super(DellRaidPhysicalDiskActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-pd-actions`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="physical-disk action to preview or invoke",
        )
        cmd_parser.add_argument(
            "--disk-fqdd",
            dest="disk_fqdd",
            default=None,
            help="physical disk FQDD for the selected action",
        )
        cmd_parser.add_argument(
            "--state",
            choices=("Offline", "Online"),
            default=None,
            help="physical disk state for --action state",
        )
        cmd_parser.add_argument(
            "--force-remove",
            dest="force_remove",
            choices=("No", "Yes"),
            default="No",
            help="ForceRemove payload value for --action prepare-remove",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the selected DellRaidService POST; otherwise preview it",
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
            "dell-raid-pd-actions",
            "preview or run Dell RAID physical disk actions",
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

    @classmethod
    def _oem_dell_link(cls, resource, key):
        """Return a Dell OEM link from ``Links.Oem.Dell`` or ``Oem.Dell``.

        :param resource: Redfish resource body to inspect.
        :param key: Dell OEM link name.
        :return: the linked URI, or None when absent.
        """
        links = (resource or {}).get("Links") or {}
        linked = cls._link(((links.get("Oem") or {}).get("Dell") or {}), key)
        if linked:
            return linked
        return cls._link((((resource or {}).get("Oem") or {}).get("Dell") or {}), key)

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
    def _action_allowables(action):
        """Return inline allowable values for one discovered action.

        :param action: discovered RedfishAction.
        :return: mapping of payload field to sorted allowable values.
        """
        args = getattr(action, "args", {}) or {}
        return {key: sorted(values or []) for key, values in args.items()}

    def _system_uri(self):
        """Return the selected ComputerSystem URI.

        :return: manager-selected system URI, or Dell default fallback.
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
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _raid_service(self, do_async):
        """Resolve and read the DellRaidService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on failure.
        """
        system_uri = self._system_uri()
        system = self._read_optional(system_uri, do_async) or {}
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        candidates = [
            self._oem_dell_link(system, "DellRaidService"),
            f"{system_uri}/{_SERVICE_SUFFIX}",
            f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService",
        ]
        seen = set()
        for uri in candidates:
            if not uri or uri in seen:
                continue
            seen.add(uri)
            data = self._read_optional(uri, do_async)
            if data is not None:
                return uri, data
        fallback = next((uri for uri in candidates if uri), f"{system_uri}/{_SERVICE_SUFFIX}")
        return (
            fallback,
            CommandResult(
                None,
                None,
                None,
                "DellRaidService is not available on this host",
            ),
        )

    def _drive_candidates(self, do_async):
        """Collect physical drive candidate identifiers from Storage resources.

        :param do_async: issue queries over the async Redfish path.
        :return: list of physical drive candidate metadata.
        """
        system_uri = self._system_uri()
        system = self._read_optional(system_uri, do_async) or {}
        storage_uri = self._link(system, "Storage") or f"{system_uri}/Storage"
        storage_collection = self._read_optional(storage_uri, do_async) or {}
        drives = []
        for member in storage_collection.get("Members") or []:
            storage_member_uri = self._link({"member": member}, "member")
            if not storage_member_uri:
                continue
            storage = self._read_optional(storage_member_uri, do_async) or {}
            for drive_link in storage.get("Drives") or []:
                drive_uri = self._link({"drive": drive_link}, "drive")
                drive = self._read_optional(drive_uri, do_async) or {}
                dell_drive = (
                    (((drive.get("Oem") or {}).get("Dell") or {})
                     .get("DellPhysicalDisk") or {})
                )
                drives.append({
                    "id": drive.get("Id") or (drive_uri or "").rsplit("/", 1)[-1],
                    "uri": drive_uri,
                    "media_type": drive.get("MediaType"),
                    "protocol": drive.get("Protocol"),
                    "raid_status": dell_drive.get("RaidStatus"),
                    "state": (drive.get("Status") or {}).get("State"),
                })
        return drives

    def _discover_rows(self, do_async):
        """Discover available physical-disk action targets and metadata.

        :param do_async: issue underlying queries over the async path.
        :return: tuple of service URI, discovered action map, and rows.
        """
        raid_uri, service = self._raid_service(do_async)
        if isinstance(service, CommandResult):
            return raid_uri, {}, service
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _ACTION_SPECS.values():
            action = actions.get(spec.action_name)
            target = targets.get(spec.full_type)
            if not target:
                continue
            rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Resource": raid_uri,
                "Target": target,
                "RequiredPayload": list(spec.required_payload),
                "Parameters": self._action_allowables(action),
            })
        return raid_uri, actions, rows

    def _payload(self, spec, disk_fqdd, state, force_remove):
        """Build a payload for a selected physical-disk action.

        :param spec: selected action metadata.
        :param disk_fqdd: physical disk FQDD.
        :param state: ChangePDState target state.
        :param force_remove: PrepareToRemove ForceRemove value.
        :return: payload dict accepted by DellRaidService.
        :raises InvalidArgument: when required fields are missing.
        """
        payload = {"TargetFQDD": self._clean_required(disk_fqdd, "disk FQDD")}
        if spec.selector == "state":
            payload["State"] = self._clean_required(state, "state")
        elif spec.selector == "prepare-remove":
            payload["ForceRemove"] = self._clean_required(force_remove, "force remove")
        return payload

    def execute(self,
                action: Optional[str] = None,
                disk_fqdd: Optional[str] = None,
                state: Optional[str] = None,
                force_remove: Optional[str] = "No",
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run DellRaidService physical-disk actions.

        :param action: selected action: ``state``, ``prepare-remove``, or ``rebuild``.
        :param disk_fqdd: physical disk FQDD.
        :param state: target state for ``--action state``.
        :param force_remove: ForceRemove value for ``--action prepare-remove``.
        :param confirm: authorize the destructive DellRaidService POST.
        :param dry_run: resolve the target without POSTing; overrides ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        raid_uri, actions, rows = self._discover_rows(bool(do_async))
        if isinstance(rows, CommandResult):
            return rows
        if action is None and disk_fqdd is None:
            return CommandResult(
                {
                    "raid_service": raid_uri,
                    "actions": rows,
                    "candidates": self._drive_candidates(bool(do_async)),
                },
                actions,
                None,
                None,
            )

        selected = self._clean_required(action, "action")
        if selected not in _ACTION_SPECS:
            raise InvalidArgument(
                "action must be one of: prepare-remove, rebuild, state"
            )
        spec = _ACTION_SPECS[selected]
        if not any(row["Action"] == selected for row in rows):
            return CommandResult(
                {
                    "raid_service": raid_uri,
                    "action": spec.full_type,
                    "available": rows,
                },
                actions,
                None,
                f"action '{spec.full_type}' not found on {raid_uri}",
            )
        return self.invoke_action(
            raid_uri,
            spec.action_name,
            payload=self._payload(spec, disk_fqdd, state, force_remove),
            full_action_type=spec.full_type,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
