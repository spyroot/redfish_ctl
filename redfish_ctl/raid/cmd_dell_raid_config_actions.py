"""Preview or run selected DellRaidService configuration actions.

    redfish_ctl dell-raid-config-actions
    redfish_ctl dell-raid-config-actions \
        --action set-boot-vd --target-fqdd Disk.Virtual.0 --confirm
    redfish_ctl dell-raid-config-actions \
        --action set-asset-name --asset-name RackA-Drawer2 --confirm

The command resolves action targets from the DellRaidService ``Actions`` block
and previews by default. Selected actions change storage configuration metadata
and only POST when ``--confirm`` is supplied.
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi


@dataclass(frozen=True)
class _DellRaidConfigActionSpec:
    selector: str
    full_type: str
    action_name: str
    description: str
    required: tuple[str, ...]


_ACTION_SPECS = {
    "set-asset-name": _DellRaidConfigActionSpec(
        selector="set-asset-name",
        full_type="#DellRaidService.SetAssetName",
        action_name="SetAssetName",
        description="set the Dell RAID enclosure asset name",
        required=("AssetName",),
    ),
    "set-boot-vd": _DellRaidConfigActionSpec(
        selector="set-boot-vd",
        full_type="#DellRaidService.SetBootVD",
        action_name="SetBootVD",
        description="set a virtual disk as the boot virtual disk",
        required=("TargetFQDD",),
    ),
}


class DellRaidConfigActions(RedfishManagerBase,
                            scm_type=ApiRequestType.DellRaidConfigActions,
                            name="dell-raid-config-actions",
                            metaclass=Singleton):
    """Preview or run selected DellRaidService configuration actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-config-actions command."""
        super(DellRaidConfigActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-config-actions`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="Dell RAID configuration action to preview or run",
        )
        cmd_parser.add_argument(
            "--target-fqdd",
            dest="target_fqdd",
            default=None,
            help="TargetFQDD payload value for virtual-disk actions",
        )
        cmd_parser.add_argument(
            "--asset-name",
            dest="asset_name",
            default=None,
            help="AssetName payload value for set-asset-name",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the selected POST; without it the command previews",
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
            "dell-raid-config-actions",
            "command selected Dell RAID configuration actions",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property."""
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell_links(resource):
        """Return ``Links.Oem.Dell`` from a Redfish resource."""
        links = resource.get("Links") if isinstance(resource, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, returning an empty dict on read failure."""
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _raid_service_uri(self, do_async):
        """Resolve DellRaidService from the selected ComputerSystem OEM links."""
        system_uri = self.idrac_manage_servers
        system = self._get(system_uri, do_async)
        linked = self._link(self._dell_links(system), "DellRaidService")
        if linked:
            return linked
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        return f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService"

    def _discover_rows(self, do_async):
        """Discover the configured DellRaidService actions."""
        service_uri = self._raid_service_uri(do_async)
        service = self._get(service_uri, do_async)
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _ACTION_SPECS.values():
            target = targets.get(spec.full_type)
            if not target:
                continue
            rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Resource": service_uri,
                "Target": target,
                "Description": spec.description,
                "RequiredPayload": list(spec.required),
            })
        return service_uri, actions, rows

    @staticmethod
    def _payload(spec, target_fqdd, asset_name):
        """Build and validate the payload for one selected action."""
        values = {
            "AssetName": asset_name,
            "TargetFQDD": target_fqdd,
        }
        payload = {
            key: values[key]
            for key in spec.required
            if values.get(key) is not None
        }
        missing = [key for key in spec.required if key not in payload]
        if missing:
            raise InvalidArgument(
                f"{spec.selector} requires: {', '.join(missing)}"
            )
        return payload

    def execute(self,
                action: Optional[str] = None,
                target_fqdd: Optional[str] = None,
                asset_name: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run selected DellRaidService configuration actions."""
        service_uri, actions, rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult(rows, actions, None, None)

        spec = _ACTION_SPECS[action]
        row = next((item for item in rows if item["Action"] == action), None)
        if row is None:
            return CommandResult(
                {
                    "raid_service": service_uri,
                    "action": spec.full_type,
                    "available": rows,
                },
                actions,
                None,
                f"Dell RAID configuration action not found: {action}",
            )

        payload = self._payload(spec, target_fqdd, asset_name)
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
