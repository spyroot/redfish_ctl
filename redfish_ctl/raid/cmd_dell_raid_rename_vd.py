"""Preview or run DellRaidService RenameVD.

    redfish_ctl dell-raid-rename-vd
    redfish_ctl dell-raid-rename-vd \
        --target-fqdd Disk.Virtual.0 --name data-vd
    redfish_ctl dell-raid-rename-vd \
        --target-fqdd Disk.Virtual.0 --name data-vd --confirm

The command resolves the action target from the DellRaidService ``Actions``
block and previews by default. ``RenameVD`` changes virtual-disk metadata and
only POSTs when ``--confirm`` is supplied.
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_RENAME_VD_ACTION = "#DellRaidService.RenameVD"


class DellRaidRenameVD(IDracManager,
                       scm_type=ApiRequestType.DellRaidRenameVD,
                       name="dell-raid-rename-vd",
                       metaclass=Singleton):
    """Preview or run the DellRaidService RenameVD action."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-rename-vd command."""
        super(DellRaidRenameVD, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-rename-vd`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--target-fqdd",
            dest="target_fqdd",
            default=None,
            help="virtual disk FQDD to rename",
        )
        cmd_parser.add_argument(
            "--name",
            dest="vd_name",
            default=None,
            help="new virtual disk name",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the RenameVD POST; without it the command previews",
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
            "dell-raid-rename-vd",
            "rename a Dell RAID virtual disk",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a linked property.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell_links(resource):
        """Return ``Links.Oem.Dell`` from a Redfish resource.

        :param resource: Redfish resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = resource.get("Links") if isinstance(resource, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating optional resource gaps.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict on read failure.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _raid_service_uri(self, do_async):
        """Resolve DellRaidService from the selected ComputerSystem OEM links.

        :param do_async: issue the query on the async path when True.
        :return: DellRaidService URI.
        """
        system_uri = self.idrac_manage_servers
        system = self._get(system_uri, do_async)
        linked = self._link(self._dell_links(system), "DellRaidService")
        if linked:
            return linked
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        return f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService"

    def _discover(self, do_async):
        """Discover the DellRaidService RenameVD target.

        :param do_async: issue underlying Redfish queries on the async path when True.
        :return: tuple of (DellRaidService URI, action metadata, RenameVD target).
        """
        service_uri = self._raid_service_uri(do_async)
        service = self._get(service_uri, do_async)
        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_RENAME_VD_ACTION)
        return service_uri, actions, target

    @staticmethod
    def _payload(target_fqdd, vd_name):
        """Build and validate the RenameVD payload.

        :param target_fqdd: virtual disk FQDD to rename.
        :param vd_name: new virtual disk name.
        :return: payload dict accepted by DellRaidService.RenameVD.
        """
        missing = []
        if not target_fqdd:
            missing.append("TargetFQDD")
        if not vd_name:
            missing.append("Name")
        if missing:
            raise InvalidArgument(
                f"dell-raid-rename-vd requires: {', '.join(missing)}"
            )
        return {"TargetFQDD": target_fqdd, "Name": vd_name}

    def execute(self,
                target_fqdd: Optional[str] = None,
                vd_name: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run DellRaidService RenameVD.

        :param target_fqdd: virtual disk FQDD to rename.
        :param vd_name: new virtual disk name.
        :param confirm: send the RenameVD POST when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        service_uri, actions, target = self._discover(bool(do_async))
        if not target_fqdd and not vd_name:
            rows = []
            if target:
                rows.append({
                    "Action": "rename-vd",
                    "FullType": _RENAME_VD_ACTION,
                    "Resource": service_uri,
                    "Target": target,
                    "RequiredPayload": ["TargetFQDD", "Name"],
                })
            return CommandResult(rows, actions, None, None)

        if target is None:
            available = sorted(self._flatten_action_targets(
                self._get(service_uri, bool(do_async))
            ))
            return CommandResult(
                {
                    "raid_service": service_uri,
                    "action": _RENAME_VD_ACTION,
                    "available": available,
                },
                actions,
                None,
                "Dell RAID RenameVD action not found",
            )

        payload = self._payload(target_fqdd, vd_name)
        return self.invoke_action(
            service_uri,
            "RenameVD",
            payload=payload,
            full_action_type=_RENAME_VD_ACTION,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
