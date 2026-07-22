"""Preview or run guarded HPE iLO chassis OEM actions.

    redfish_ctl hpe-chassis-actions
    redfish_ctl hpe-chassis-actions --action disable-mctp
    redfish_ctl hpe-chassis-actions --action disable-mctp --confirm

The command discovers supported HPE chassis OEM action targets from the live
Redfish tree and dry-runs by default. It deliberately exposes only
``HpeServerChassis.DisableMCTPOnServer``; adjacent factory-reset actions are not
selectable from this command.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi


@dataclass(frozen=True)
class _HpeChassisActionSpec:
    """Static selector metadata for one HPE chassis action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "disable-mctp": _HpeChassisActionSpec(
        selector="disable-mctp",
        full_type="#HpeServerChassis.DisableMCTPOnServer",
        action_name="DisableMCTPOnServer",
        description="disable server-side MCTP on an HPE chassis",
    ),
}


class HpeChassisActions(IDracManager,
                        scm_type=ApiRequestType.HpeChassisActions,
                        name="hpe-chassis-actions",
                        metaclass=Singleton):
    """Discover and invoke guarded HPE iLO chassis OEM actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the hpe-chassis-actions command."""
        super(HpeChassisActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``hpe-chassis-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="HPE chassis action to preview or send; omit to list targets",
        )
        cmd_parser.add_argument(
            "--chassis-uri",
            dest="chassis_uri",
            default=None,
            help="specific Chassis resource URI when more than one target advertises the action",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="send the selected chassis action instead of dry-running it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return cmd_parser, "hpe-chassis-actions", "command run guarded HPE chassis actions"

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
    def _members(data):
        """Return collection member ``@odata.id`` values.

        :param data: Redfish collection body.
        :return: list of member URIs.
        """
        if not isinstance(data, dict):
            return []
        return [
            item["@odata.id"]
            for item in data.get("Members", [])
            if isinstance(item, dict) and isinstance(item.get("@odata.id"), str)
        ]

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating missing optional resources.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _chassis_collection_uri(self, do_async):
        """Resolve the Chassis collection URI from ServiceRoot.

        :param do_async: run the ServiceRoot query asynchronously when True.
        :return: linked Chassis collection URI, or the standard fallback.
        """
        root = self._get(RedfishApi.Version, do_async)
        return self._link(root, "Chassis") or f"{RedfishApi.Version}/Chassis"

    def _chassis_uris(self, do_async):
        """Return Chassis member URIs from the Redfish Chassis collection.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of Chassis resource URIs.
        """
        return self._members(self._get(self._chassis_collection_uri(do_async), do_async))

    def _discover_rows(self, do_async):
        """Discover available supported HPE chassis actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available HPE chassis action rows.
        """
        rows = []
        for chassis_uri in self._chassis_uris(do_async):
            chassis = self._get(chassis_uri, do_async)
            targets = self._flatten_action_targets(chassis)
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Chassis": chassis_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, chassis_uri):
        """Filter discovered rows by action selector and optional Chassis URI.

        :param rows: discovered HPE chassis action rows.
        :param action: selected action name.
        :param chassis_uri: optional Chassis resource URI selector.
        :return: matching rows.
        """
        matches = [row for row in rows if row["Action"] == action]
        if chassis_uri:
            normalized = chassis_uri.rstrip("/")
            matches = [
                row for row in matches
                if row["Chassis"].rstrip("/") == normalized
            ]
        return matches

    def execute(self,
                action: Optional[str] = None,
                chassis_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or send guarded HPE chassis OEM actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param chassis_uri: optional Chassis URI to disambiguate multiple targets.
        :param confirm: send the selected chassis action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = self._matches(rows, action, chassis_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"HPE chassis action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple HPE chassis-action targets found; pass --chassis-uri",
            )

        row = matches[0]
        spec = _ACTION_SPECS[action]
        result = self.invoke_action(
            row["Chassis"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = "HPE chassis action requires --confirm"
        return result
