"""Guarded Redfish Drive.SecureErase command.

    redfish_ctl drive-secure-erase --controller RAID.Integrated.1-1
    redfish_ctl drive-secure-erase --controller RAID.Integrated.1-1 --drive_id Disk.Bay.0
    redfish_ctl drive-secure-erase --drive_uri /redfish/v1/Chassis/NVME_M2_0/Drives/NVMe_SSD_210
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument, UnsupportedAction
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton

_SECURE_ERASE_ACTION = "#Drive.SecureErase"


def _last_segment(uri: str) -> str:
    """Return the final path segment from a Redfish URI.

    :param uri: Redfish URI or id to reduce.
    :return: the trailing segment without slashes.
    """
    return str(uri).rstrip("/").split("/")[-1]


class DriveSecureErase(
    IDracManager,
    scm_type=ApiRequestType.DriveSecureErase,
    name="drive-secure-erase",
    metaclass=Singleton,
):
    """Preview or run a guarded Redfish Drive.SecureErase action."""

    def __init__(self, *args, **kwargs):
        """Initialize the drive-secure-erase command."""
        super(DriveSecureErase, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the drive-secure-erase command and flags.

        :param cls: the CLI manager class providing the base parser.
        :return: tuple of parser, command name, and help text.
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--controller",
            required=False,
            type=str,
            default=None,
            help="Storage controller id used to resolve --drive_id",
        )
        cmd_parser.add_argument(
            "--drive_id",
            required=False,
            type=str,
            default=None,
            help="Drive id from --controller; omit to list controller drives",
        )
        cmd_parser.add_argument(
            "--drive_uri",
            required=False,
            type=str,
            default=None,
            help="Exact Redfish Drive URI for non-Storage drive paths",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="authorize the secure erase action",
        )
        cmd_parser.add_argument(
            "--i-understand-irreversible",
            action="store_true",
            dest="confirm_irreversible",
            default=False,
            help="required with --confirm because secure erase destroys data",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="force a preview even when confirmation flags are present",
        )
        return (
            cmd_parser,
            "drive-secure-erase",
            "run Redfish Drive.SecureErase with irreversible guards",
        )

    def _storage(self, controller: str, do_async: bool = False) -> dict:
        """Fetch a Storage resource by controller id.

        :param controller: Storage controller id.
        :param do_async: note async will subscribe to an event loop.
        :return: Storage resource data.
        :raises InvalidArgument: if no controller was supplied.
        """
        if not controller:
            raise InvalidArgument("provide --controller or --drive_uri")
        result = self.sync_invoke(
            ApiRequestType.StorageViewQuery,
            "storage_get",
            controller=controller,
            do_async=do_async,
        )
        return result.data or {}

    def _drive_members(self, storage: dict) -> list[dict]:
        """Return Drive link members from a Storage resource.

        :param storage: Redfish Storage resource data.
        :return: list of Drive member links.
        :raises UnsupportedAction: when no Drives list is advertised.
        """
        drives = storage.get("Drives")
        if not isinstance(drives, list):
            raise UnsupportedAction("Storage resource does not advertise Drives")
        return [
            member
            for member in drives
            if isinstance(member, dict) and member.get("@odata.id")
        ]

    def _resolve_drive_uri(self, storage: dict, drive_id: str) -> tuple[str, list[str]]:
        """Resolve a controller-local drive id to a Redfish Drive URI.

        :param storage: Redfish Storage resource data.
        :param drive_id: drive id from the Storage ``Drives`` list.
        :return: tuple of resolved URI and available ids.
        :raises InvalidArgument: if the id is empty or unknown.
        """
        if not drive_id:
            raise InvalidArgument("provide --drive_id")
        drive_map = {
            _last_segment(str(member["@odata.id"])): str(member["@odata.id"])
            for member in self._drive_members(storage)
        }
        available = sorted(drive_map)
        if drive_id not in drive_map:
            raise InvalidArgument(f"Drive {drive_id} not found, available {available}")
        return drive_map[drive_id], available

    def _drive_candidates(self, storage: dict, do_async: bool = False) -> list[dict]:
        """List Storage drives and whether they advertise SecureErase.

        :param storage: Redfish Storage resource data.
        :param do_async: note async will subscribe to an event loop.
        :return: candidate drive summaries.
        """
        candidates = []
        for member in self._drive_members(storage):
            uri = str(member["@odata.id"])
            data = self.base_query(uri, do_async=do_async).data or {}
            targets = self._flatten_action_targets(data)
            candidates.append(
                {
                    "drive_id": str(data.get("Id") or _last_segment(uri)),
                    "uri": uri,
                    "secure_erase": _SECURE_ERASE_ACTION in targets,
                    "target": targets.get(_SECURE_ERASE_ACTION),
                }
            )
        return candidates

    def _validate_drive_uri(self, drive_uri: str) -> str:
        """Validate and normalize an exact Drive URI.

        :param drive_uri: Redfish Drive resource URI.
        :return: normalized URI.
        :raises InvalidArgument: if the URI is not a Redfish path.
        """
        uri = str(drive_uri or "").strip()
        if not uri.startswith("/redfish/"):
            raise InvalidArgument("--drive_uri must be an absolute Redfish URI")
        return uri

    def execute(
            self,
            controller: Optional[str] = None,
            drive_id: Optional[str] = None,
            drive_uri: Optional[str] = None,
            confirm: Optional[bool] = False,
            confirm_irreversible: Optional[bool] = False,
            dry_run: Optional[bool] = False,
            do_async: Optional[bool] = False,
            **kwargs) -> CommandResult:
        """Preview or execute Drive.SecureErase.

        With ``--controller`` and no ``--drive_id``, the command lists drives and
        whether they advertise ``#Drive.SecureErase`` without POSTing. With a
        drive id or exact Drive URI, the command previews by default and only
        POSTs when both confirmation flags are present.

        :param controller: Storage controller id used for drive-id resolution.
        :param drive_id: controller-local drive id.
        :param drive_uri: exact Redfish Drive resource URI.
        :param confirm: authorize the irreversible action.
        :param confirm_irreversible: extra acknowledgement required for secure erase.
        :param dry_run: force a preview even when confirmation flags are present.
        :param do_async: note async will subscribe to an event loop.
        :return: CommandResult with listed candidates, dry-run preview, or POST result.
        :raises InvalidArgument: for ambiguous or missing target flags.
        """
        if drive_uri and (controller or drive_id):
            raise InvalidArgument(
                "--drive_uri cannot be combined with --controller or --drive_id"
            )

        if drive_uri:
            uri = self._validate_drive_uri(drive_uri)
        else:
            storage = self._storage(str(controller or ""), do_async=bool(do_async))
            if not drive_id:
                return CommandResult(
                    {
                        "controller": controller,
                        "drives": self._drive_candidates(
                            storage,
                            do_async=bool(do_async),
                        ),
                        "hint": "pass --drive_id to preview Drive.SecureErase",
                    },
                    None,
                    None,
                    None,
                )
            uri, _ = self._resolve_drive_uri(storage, str(drive_id))

        return self.invoke_action(
            uri,
            "SecureErase",
            payload={},
            full_action_type=_SECURE_ERASE_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
            confirm_irreversible=bool(confirm_irreversible),
        )
