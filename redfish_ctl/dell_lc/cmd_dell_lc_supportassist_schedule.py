"""Preview or update Dell Lifecycle Controller SupportAssist schedules.

    redfish_ctl dell-lc-supportassist-schedule
    redfish_ctl dell-lc-supportassist-schedule --action set --recurrence Weekly
    redfish_ctl dell-lc-supportassist-schedule --action clear --confirm

The command resolves the SupportAssist auto-collection schedule actions from
the Dell Lifecycle Controller service. Without ``--action`` it lists available
targets. Set and clear operations preview by default; ``--confirm`` is required
before a POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_SERVICE_NAME = "DellLCService"
_DEFAULT_SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
_ACTION_SPECS = {
    "clear": {
        "type": "#DellLCService.SupportAssistClearAutoCollectSchedule",
        "name": "SupportAssistClearAutoCollectSchedule",
    },
    "set": {
        "type": "#DellLCService.SupportAssistSetAutoCollectSchedule",
        "name": "SupportAssistSetAutoCollectSchedule",
    },
}


class DellLcSupportAssistSchedule(
        RedfishManagerBase,
        scm_type=ApiRequestType.DellLcSupportAssistSchedule,
        name="dell-lc-supportassist-schedule",
        metaclass=Singleton):
    """Discover and invoke DellLCService SupportAssist schedule actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-supportassist-schedule command."""
        super(DellLcSupportAssistSchedule, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-supportassist-schedule`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=tuple(_ACTION_SPECS),
            default=None,
            help="SupportAssist schedule action to preview or run",
        )
        cmd_parser.add_argument(
            "--recurrence",
            default=None,
            help="recurrence for --action set; allowed values come from Redfish",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellLCService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the schedule action instead of previewing it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return (
            cmd_parser,
            "dell-lc-supportassist-schedule",
            "command update Dell Lifecycle Controller SupportAssist schedule",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell_oem_links(manager):
        """Return the Dell block under ``Manager.Links.Oem``.

        :param manager: Redfish Manager resource body.
        :return: Dell OEM links dict, or an empty dict.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating optional-resource misses.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DellLCService URIs in discovery-first order.

        :param do_async: issue manager queries on the async path when True.
        :return: de-duplicated candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(self._dell_oem_links(manager), _SERVICE_NAME)
            if service_uri:
                uris.append(service_uri)
            uris.append(manager_uri.rstrip("/") + f"/Oem/Dell/{_SERVICE_NAME}")
        uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uri for uri in uris if uri))

    @staticmethod
    def _allowed_recurrences(actions):
        """Return advertised SupportAssist schedule recurrence values.

        :param actions: discovered Redfish action map for a DellLCService body.
        :return: sorted list of allowed recurrence strings.
        """
        action = actions.get(_ACTION_SPECS["set"]["name"])
        allowed = tuple(getattr(action, "args", {}).get("Recurrence", ()) or ())
        return sorted(allowed)

    def _rows_for(self, resource_uri, do_async):
        """Build discovered SupportAssist schedule rows for one service URI.

        :param resource_uri: candidate DellLCService URI.
        :param do_async: issue the service query on the async path when True.
        :return: discovered rows, possibly empty when actions are absent.
        """
        service = self._get(resource_uri, do_async)
        if not service:
            return []
        targets = self._flatten_action_targets(service)
        actions = self.discover_redfish_actions(self, service)
        allowed_recurrences = self._allowed_recurrences(actions)
        rows = []
        for selector, spec in _ACTION_SPECS.items():
            target = targets.get(spec["type"])
            if not target:
                continue
            row = {
                "Resource": resource_uri,
                "Selector": selector,
                "Action": spec["type"],
                "Target": target,
            }
            if selector == "set":
                row["AllowedRecurrences"] = allowed_recurrences
            rows.append(row)
        return rows

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell LC SupportAssist schedule action targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: list of discovered target rows.
        """
        uris = [resource_uri] if resource_uri else self._service_uris(do_async)
        rows = []
        for uri in dict.fromkeys(uris):
            rows.extend(self._rows_for(uri, do_async))
        return rows

    @staticmethod
    def _select_row(rows, action):
        """Return the discovered row for a requested schedule action.

        :param rows: discovered SupportAssist schedule rows.
        :param action: requested action selector.
        :return: row dict, or None when absent.
        """
        for row in rows:
            if row["Selector"] == action:
                return row
        return None

    def execute(self,
                action: Optional[str] = None,
                recurrence: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke SupportAssist schedule actions.

        :param action: optional action selector, ``set`` or ``clear``.
        :param recurrence: recurrence value required by ``--action set``.
        :param resource_uri: optional DellLCService URI override.
        :param confirm: authorize the action POST.
        :param dry_run: force a preview even when ``confirm`` is true.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        """
        rows = self._discover_rows(bool(do_async), resource_uri=resource_uri)
        if action is None:
            return CommandResult(
                {"supportassist_schedule_targets": rows},
                None,
                None,
                None,
            )
        if action not in _ACTION_SPECS:
            return CommandResult(
                {"valid_actions": sorted(_ACTION_SPECS)},
                None,
                None,
                f"invalid SupportAssist schedule action: {action}",
            )
        row = self._select_row(rows, action)
        if row is None:
            return CommandResult(
                {"action": _ACTION_SPECS[action]["type"], "available": rows},
                None,
                None,
                "Dell LC SupportAssist schedule action not found",
            )
        if action == "set" and not recurrence:
            return CommandResult(
                {"required": ["Recurrence"], "action": row["Action"]},
                None,
                None,
                "SupportAssist schedule set requires --recurrence",
            )

        payload = {"Recurrence": recurrence} if action == "set" else {}
        spec = _ACTION_SPECS[action]
        return self.invoke_action(
            row["Resource"],
            spec["name"],
            payload=payload,
            full_action_type=spec["type"],
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
