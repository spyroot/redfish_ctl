"""Create and delete Redfish Volume resources through the Storage collection."""
from abc import abstractmethod
from typing import Iterable, Optional

from ..cmd_exceptions import InvalidArgument, UnsupportedAction
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


def _last_segment(uri: str) -> str:
    """Return the last Redfish URI segment."""
    return str(uri).rstrip("/").split("/")[-1]


def _as_list(values: Optional[Iterable[str]]) -> list[str]:
    """Normalize argparse and direct-call values into a string list."""
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


def _collect_supported_raid_types(payload: object) -> list[str]:
    """Find advertised RAID type values in a Redfish payload."""
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
    """Build the standard DMTF Volume create payload."""
    return {
        "Name": name,
        "RAIDType": raid_type,
        "Links": {"Drives": [{"@odata.id": uri} for uri in drive_uris]},
    }


class _VolumeMutationBase(IDracManager):
    """Shared Storage/Volume collection helpers for guarded mutations."""

    def _storage(self, controller: str, do_async: bool = False) -> dict:
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
        volumes = storage.get("Volumes")
        if not isinstance(volumes, dict) or not volumes.get("@odata.id"):
            raise UnsupportedAction(
                f"Storage controller {controller} does not advertise a Volumes link"
            )
        return str(volumes["@odata.id"])

    def _resolve_drive_uris(self, storage: dict, drives: list[str]) -> list[str]:
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
        super(VolumeCreate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
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
        super(VolumeDelete, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
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
