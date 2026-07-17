"""Create and delete Redfish Volume resources through the Storage collection.

Both commands preview by default and require ``--confirm`` to mutate.

    redfish_ctl volume-create --controller RAID.Integrated.1-1 --name vol0 --raid_type RAID1 --drive Disk.Bay.0 --drive Disk.Bay.1 --confirm
    redfish_ctl volume-delete --controller RAID.Integrated.1-1 --volume_id Volume-1 --confirm --confirm_volume_id Volume-1
    redfish_ctl volume-check-consistency --controller RAID.Integrated.1-1 --volume_id Volume-1 --confirm
"""
from abc import abstractmethod
from typing import Iterable, Optional

from ..cmd_exceptions import InvalidArgument, UnsupportedAction
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_CHECK_CONSISTENCY_ACTION = "#Volume.CheckConsistency"


def _last_segment(uri: str) -> str:
    """Return the last path segment of a Redfish URI.

    :param uri: Redfish URI or id to reduce to its final segment.
    :return: the trailing path segment with surrounding slashes removed.
    """
    return str(uri).rstrip("/").split("/")[-1]


def _as_list(values: Optional[Iterable[str]]) -> list[str]:
    """Normalize argparse and direct-call values into a string list.

    :param values: None, a single string, or an iterable of string values.
    :return: a list of strings; empty when values is None.
    """
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


def _collect_supported_raid_types(payload: object) -> list[str]:
    """Find advertised RAID type values in a Redfish payload.

    Walks the payload collecting ``SupportedRAIDTypes`` and
    ``RAIDType@Redfish.AllowableValues`` list entries.

    :param payload: a Redfish Storage/Volume structure (dict, list, or scalar).
    :return: sorted, de-duplicated list of advertised RAID type strings.
    """
    supported: list[str] = []
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {
                    "SupportedRAIDTypes",
                    "RAIDType@Redfish.AllowableValues",
                } and isinstance(value, list):
                    supported.extend(str(entry) for entry in value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return sorted(set(supported))


def build_volume_payload(name: str, raid_type: str, drive_uris: list[str]) -> dict:
    """Build the standard DMTF Volume create payload.

    :param name: volume name to send in the create request.
    :param raid_type: Redfish RAIDType value, for example RAID1.
    :param drive_uris: Redfish drive URIs to link into the volume.
    :return: the Volume create payload dict.
    """
    return {
        "Name": name,
        "RAIDType": raid_type,
        "Links": {"Drives": [{"@odata.id": uri} for uri in drive_uris]},
    }


class _VolumeMutationBase(RedfishManagerBase):
    """Shared Storage/Volume collection helpers for guarded mutations."""

    def _storage(self, controller: str, do_async: bool = False) -> dict:
        """Fetch the Storage resource for a controller.

        :param controller: storage controller id, for example RAID.Integrated.1-1.
        :param do_async: note async will subscribe to an event loop.
        :return: the Storage resource dict; empty dict when no data is returned.
        :raises InvalidArgument: if controller is empty.
        """
        if not controller:
            raise InvalidArgument("provide --controller")
        result = self.sync_invoke(
            ApiRequestType.StorageViewQuery,
            "storage_get",
            controller=controller,
            do_async=do_async,
        )
        return result.data or {}

    def _volumes_uri(self, storage: dict, controller: str) -> str:
        """Return the Volumes collection URI from a Storage resource.

        :param storage: a Redfish Storage resource dict.
        :param controller: controller id, used only in the error message.
        :return: the Volumes collection ``@odata.id`` URI.
        :raises UnsupportedAction: if the controller advertises no Volumes link.
        """
        volumes = storage.get("Volumes")
        if not isinstance(volumes, dict) or not volumes.get("@odata.id"):
            raise UnsupportedAction(
                f"Storage controller {controller} does not advertise a Volumes link"
            )
        return str(volumes["@odata.id"])

    def _resolve_drive_uris(self, storage: dict, drives: list[str]) -> list[str]:
        """Resolve drive ids or URIs against a Storage resource's drives.

        :param storage: a Redfish Storage resource dict listing member drives.
        :param drives: drive ids or full Redfish drive URIs to resolve.
        :return: the list of resolved Redfish drive URIs.
        :raises InvalidArgument: if drives is empty or a drive id is unknown.
        """
        if not drives:
            raise InvalidArgument("provide at least one --drive")
        drive_map = {
            _last_segment(member["@odata.id"]): member["@odata.id"]
            for member in storage.get("Drives", [])
            if isinstance(member, dict) and member.get("@odata.id")
        }
        resolved = []
        for drive in drives:
            if drive.startswith("/redfish/"):
                resolved.append(drive)
                continue
            if drive not in drive_map:
                available = sorted(drive_map)
                raise InvalidArgument(
                    f"Drive {drive} not found, available {available}"
                )
            resolved.append(drive_map[drive])
        return resolved

    def _volume_collection(self, controller: str, do_async: bool = False) -> dict:
        """Fetch the Volume collection for a controller.

        :param controller: storage controller id whose volumes to fetch.
        :param do_async: note async will subscribe to an event loop.
        :return: the Volume collection dict; empty dict when no data is returned.
        """
        result = self.sync_invoke(
            ApiRequestType.VolumeQuery,
            "vol_query",
            dev_id=controller,
            do_async=do_async,
        )
        return result.data or {}

    def _resolve_volume_uri(
            self,
            controller: str,
            volume_id: str,
            do_async: bool = False) -> tuple[str, str, list[str]]:
        """Resolve a volume id or URI to its Redfish Volume URI.

        :param controller: storage controller id owning the volume.
        :param volume_id: volume id or full Redfish Volume URI to resolve.
        :param do_async: note async will subscribe to an event loop.
        :return: tuple of (volume URI, resolved volume id, sorted available ids).
        :raises InvalidArgument: if volume_id is empty or not found.
        """
        if not volume_id:
            raise InvalidArgument("provide --volume_id")
        collection = self._volume_collection(controller, do_async=do_async)
        members = collection.get("Members", [])
        available: dict[str, str] = {}
        for member in members:
            if not isinstance(member, dict) or not member.get("@odata.id"):
                continue
            uri = str(member["@odata.id"])
            member_id = str(member.get("Id") or _last_segment(uri))
            available[member_id] = uri
            available[uri] = uri
        if volume_id in available:
            uri = available[volume_id]
            return uri, _last_segment(uri), sorted(
                key for key in available if not key.startswith("/redfish/")
            )
        ids = sorted(key for key in available if not key.startswith("/redfish/"))
        raise InvalidArgument(f"Volume {volume_id} not found, available {ids}")


class VolumeCreate(
    _VolumeMutationBase,
    scm_type=ApiRequestType.VolumeCreate,
    name="volume-create",
    metaclass=Singleton,
):
    """Create a Redfish Volume through a Storage resource's Volumes collection."""

    def __init__(self, *args, **kwargs):
        """Initialize the volume-create command."""
        super(VolumeCreate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the volume-create command and its flags.

        :param cls: the CLI manager class providing the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--controller", required=True, type=str,
            help="Storage controller id, for example RAID.Integrated.1-1")
        cmd_parser.add_argument(
            "--name", required=True, type=str,
            dest="volume_name",
            help="Volume Name to send in the create payload")
        cmd_parser.add_argument(
            "--raid_type", required=True, type=str,
            help="Redfish RAIDType, for example RAID1")
        cmd_parser.add_argument(
            "--drive", required=True, action="append", dest="drives",
            help="Drive id or Redfish drive URI; repeat for multiple drives")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="actually create the volume; otherwise preview only")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="force a preview even when --confirm is present")
        return cmd_parser, "volume-create", "create a Redfish volume (guarded)"

    def execute(self,
                controller: str,
                volume_name: str,
                raid_type: str,
                drives: Optional[Iterable[str]] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Create a Redfish volume, previewing unless explicitly confirmed.

        :param controller: storage controller id, for example RAID.Integrated.1-1.
        :param volume_name: name to send in the Volume create payload.
        :param raid_type: Redfish RAIDType value, validated against the controller.
        :param drives: drive ids or Redfish drive URIs to include in the volume.
        :param confirm: if set (and not dry_run), actually create the volume.
        :param dry_run: force a preview even when confirm is present.
        :param do_async: note async will subscribe to an event loop.
        :return: CommandResult with the dry-run preview, or the create result.
        :raises InvalidArgument: if raid_type, drives, or controller are invalid.
        :raises UnsupportedAction: if the controller advertises no Volumes link.
        """
        storage = self._storage(controller, do_async=bool(do_async))
        target = self._volumes_uri(storage, controller)
        allowed = _collect_supported_raid_types(storage)
        if allowed and raid_type not in allowed:
            raise InvalidArgument(
                f"RAIDType {raid_type} not supported, available {allowed}"
            )
        payload = build_volume_payload(
            volume_name,
            raid_type,
            self._resolve_drive_uris(storage, _as_list(drives)),
        )
        if dry_run or not confirm:
            return CommandResult(
                {
                    "dry_run": True,
                    "action": "create",
                    "target": target,
                    "payload": payload,
                    "hint": "re-run with --confirm to create the volume",
                },
                None,
                None,
                None,
            )
        result, status = self.base_post(target, payload=payload, do_async=do_async)
        return CommandResult(
            {
                "action": "create",
                "target": target,
                "status": str(status),
                "error": result.error,
            },
            None,
            None,
            result.error,
        )


class VolumeDelete(
    _VolumeMutationBase,
    scm_type=ApiRequestType.VolumeDelete,
    name="volume-delete",
    metaclass=Singleton,
):
    """Delete a Redfish Volume member after explicit confirmation."""

    def __init__(self, *args, **kwargs):
        """Initialize the volume-delete command."""
        super(VolumeDelete, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the volume-delete command and its flags.

        :param cls: the CLI manager class providing the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--controller", required=True, type=str,
            help="Storage controller id, for example RAID.Integrated.1-1")
        cmd_parser.add_argument(
            "--volume_id", required=True, type=str,
            help="Volume id or Redfish Volume URI")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="actually delete the volume; otherwise preview only")
        cmd_parser.add_argument(
            "--confirm_volume_id", required=False, type=str, default=None,
            help="repeat the volume id to authorize deletion")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="force a preview even when --confirm is present")
        return cmd_parser, "volume-delete", "delete a Redfish volume (guarded)"

    def execute(self,
                controller: str,
                volume_id: str,
                confirm: Optional[bool] = False,
                confirm_volume_id: Optional[str] = None,
                dry_run: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Delete a Redfish volume, previewing unless explicitly confirmed.

        :param controller: storage controller id owning the volume.
        :param volume_id: volume id or Redfish Volume URI to delete.
        :param confirm: if set (and not dry_run), actually delete the volume.
        :param confirm_volume_id: must match the resolved id to authorize deletion.
        :param dry_run: force a preview even when confirm is present.
        :param do_async: note async will subscribe to an event loop.
        :return: CommandResult with the preview, a mismatch error, or the delete result.
        :raises InvalidArgument: if volume_id is empty or not found.
        """
        uri, resolved_id, _ = self._resolve_volume_uri(
            controller,
            volume_id,
            do_async=bool(do_async),
        )
        if dry_run or not confirm:
            return CommandResult(
                {
                    "dry_run": True,
                    "action": "delete",
                    "target": volume_id,
                    "uri": uri,
                    "hint": "re-run with --confirm and --confirm_volume_id to delete",
                },
                None,
                None,
                None,
            )
        if confirm_volume_id != resolved_id:
            return CommandResult(
                None,
                None,
                None,
                f"confirm_volume_id must match {resolved_id}",
            )
        result, status = self.base_delete(uri, do_async=do_async)
        return CommandResult(
            {
                "action": "delete",
                "target": resolved_id,
                "uri": uri,
                "status": str(status),
                "error": result.error,
            },
            None,
            None,
            result.error,
        )


class VolumeCheckConsistency(
    _VolumeMutationBase,
    scm_type=ApiRequestType.VolumeCheckConsistency,
    name="volume-check-consistency",
    metaclass=Singleton,
):
    """Run a guarded Redfish Volume.CheckConsistency action."""

    def __init__(self, *args, **kwargs):
        """Initialize the volume-check-consistency command."""
        super(VolumeCheckConsistency, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the volume-check-consistency command and its flags.

        :param cls: the CLI manager class providing the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--controller", required=True, type=str,
            help="Storage controller id, for example RAID.Integrated.1-1")
        cmd_parser.add_argument(
            "--volume_id", required=True, type=str,
            help="Volume id or Redfish Volume URI")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="actually start the consistency check; otherwise preview only")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="force a preview even when --confirm is present")
        return (
            cmd_parser,
            "volume-check-consistency",
            "run a Redfish volume consistency check (guarded)",
        )

    def execute(self,
                controller: str,
                volume_id: str,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Run Volume.CheckConsistency, previewing unless explicitly confirmed.

        :param controller: storage controller id owning the volume.
        :param volume_id: volume id or Redfish Volume URI to check.
        :param confirm: if set (and not dry_run), actually start the check.
        :param dry_run: force a preview even when confirm is present.
        :param do_async: note async will subscribe to an event loop.
        :return: CommandResult with the preview or the action result.
        :raises InvalidArgument: if volume_id is empty or not found.
        """
        uri, _, _ = self._resolve_volume_uri(
            controller,
            volume_id,
            do_async=bool(do_async),
        )
        return self.invoke_action(
            uri,
            "CheckConsistency",
            payload={},
            full_action_type=_CHECK_CONSISTENCY_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
