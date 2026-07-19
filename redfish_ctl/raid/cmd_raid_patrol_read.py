"""Preview or run Dell RAID patrol-read actions.

    redfish_ctl dell-raid-patrol-read
    redfish_ctl dell-raid-patrol-read --action start --confirm
    redfish_ctl dell-raid-patrol-read --action stop --confirm

The command resolves the advertised ``#DellRaidService.StartPatrolRead`` and
``#DellRaidService.StopPatrolRead`` targets from DellRaidService. It previews by
default and only POSTs when ``--confirm`` is supplied.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_PATROL_ACTIONS = {
    "start": "#DellRaidService.StartPatrolRead",
    "stop": "#DellRaidService.StopPatrolRead",
}


class DellRaidPatrolRead(RedfishManagerBase,
                         scm_type=ApiRequestType.DellRaidPatrolRead,
                         name="dell-raid-patrol-read",
                         metaclass=Singleton):
    """Preview or run Dell RAID patrol-read start/stop actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-patrol-read command."""
        super(DellRaidPatrolRead, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-patrol-read`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=tuple(_PATROL_ACTIONS),
            default=None,
            help="patrol-read action to preview or run",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the selected patrol-read POST; without it the command previews",
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
            "dell-raid-patrol-read",
            "command start or stop Dell RAID patrol read",
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

    def _dell_raid_service_uri(self, do_async):
        """Resolve DellRaidService from the system OEM link or standard Dell path.

        :param do_async: issue the system query over the async Redfish path.
        :return: the DellRaidService URI to inspect for advertised actions.
        """
        try:
            system_uri = self.idrac_manage_servers
        except Exception:
            system_uri = ""
        system_uri = system_uri or "/redfish/v1/Systems/System.Embedded.1"
        try:
            system = self.base_query(system_uri, do_async=do_async).data or {}
        except Exception:
            system = {}
        dell = ((system.get("Oem") or {}).get("Dell") or {})
        return self._link(dell, "DellRaidService") or (
            f"{system_uri.rstrip('/')}/Oem/Dell/DellRaidService"
        )

    def execute(self,
                action: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and optionally invoke Dell RAID patrol-read actions.

        :param action: ``start`` or ``stop``; when omitted, list advertised targets.
        :param confirm: authorize the selected patrol-read POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing;
            overrides ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with advertised targets, preview, or execution result.
        """
        service_uri = self._dell_raid_service_uri(do_async)
        if not action:
            service = self.base_query(service_uri, do_async=do_async).data or {}
            actions = service.get("Actions") or {}
            targets = {
                name: (actions.get(full_type) or {}).get("target")
                for name, full_type in _PATROL_ACTIONS.items()
                if (actions.get(full_type) or {}).get("target")
            }
            return CommandResult(
                {
                    "service": service_uri,
                    "actions": targets,
                    "available": sorted(actions),
                },
                None,
                None,
                None,
            )

        full_type = _PATROL_ACTIONS[action]
        return self.invoke_action(
            service_uri,
            full_type.rsplit(".", 1)[-1],
            payload={},
            full_action_type=full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
