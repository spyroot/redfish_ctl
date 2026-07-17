"""Preview or run selected HPE iLO manager OEM Redfish actions.

    redfish_ctl hpe-manager-actions
    redfish_ctl hpe-manager-actions --action retry-cloud-connect
    redfish_ctl hpe-manager-actions --action clear-rest-api-state --confirm

The command discovers supported HPE OEM actions from each Manager resource's
``Oem.Hpe.Actions`` block. Factory reset, manager reset, NVRAM clear, firmware
recovery, and iLO disable actions are deliberately not exposed by this command.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton


@dataclass(frozen=True)
class _HpeManagerActionSpec:
    """Static selector metadata for one supported HPE Manager OEM action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "clear-hotkeys": _HpeManagerActionSpec(
        selector="clear-hotkeys",
        full_type="#HpeiLO.ClearHotKeys",
        action_name="ClearHotKeys",
        description="clear configured Integrated Remote Console hot keys",
    ),
    "clear-rest-api-state": _HpeManagerActionSpec(
        selector="clear-rest-api-state",
        full_type="#HpeiLO.ClearRestApiState",
        action_name="ClearRestApiState",
        description="clear iLO REST API state data",
    ),
    "disable-cloud-connect": _HpeManagerActionSpec(
        selector="disable-cloud-connect",
        full_type="#HpeiLO.DisableCloudConnect",
        action_name="DisableCloudConnect",
        description="disable HPE cloud-connect integration",
    ),
    "enable-cloud-connect": _HpeManagerActionSpec(
        selector="enable-cloud-connect",
        full_type="#HpeiLO.EnableCloudConnect",
        action_name="EnableCloudConnect",
        description="enable HPE cloud-connect integration",
    ),
    "retry-cloud-connect": _HpeManagerActionSpec(
        selector="retry-cloud-connect",
        full_type="#HpeiLO.RetryCloudConnect",
        action_name="RetryCloudConnect",
        description="retry HPE cloud-connect registration",
    ),
}


class HpeManagerActions(RedfishManagerBase,
                        scm_type=ApiRequestType.HpeManagerActions,
                        name="hpe-manager-actions",
                        metaclass=Singleton):
    """Discover and invoke selected HPE iLO Manager OEM actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the hpe-manager-actions command."""
        super(HpeManagerActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``hpe-manager-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="HPE Manager OEM action to preview or run; omit to list targets",
        )
        cmd_parser.add_argument(
            "--manager-uri",
            dest="manager_uri",
            default=None,
            help="specific Manager resource URI when more than one target advertises the action",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="run the selected manager action instead of dry-running it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return cmd_parser, "hpe-manager-actions", "command run HPE iLO manager OEM actions"

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

    def _manager_uris(self):
        """Return discovered Manager resource URIs.

        :return: list of Manager resource URIs.
        """
        uris = self.discover_manager_ids() or []
        return [uri for uri in uris if isinstance(uri, str) and uri]

    def _target_for(self, manager_uri, spec, do_async):
        """Return the advertised target URI for an HPE Manager OEM action.

        :param manager_uri: candidate Manager resource URI.
        :param spec: HPE manager-action selector metadata.
        :param do_async: run the manager query asynchronously when True.
        :return: action target URI, or None when absent.
        """
        manager = self._get(manager_uri, do_async)
        targets = self._flatten_action_targets(manager)
        return targets.get(spec.full_type)

    def _discover_rows(self, do_async):
        """Discover supported HPE Manager OEM action rows.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available manager-action rows.
        """
        rows = []
        for manager_uri in self._manager_uris():
            for spec in _ACTION_SPECS.values():
                target = self._target_for(manager_uri, spec, do_async)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": manager_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, manager_uri):
        """Filter discovered rows by action selector and optional manager URI.

        :param rows: discovered HPE manager-action rows.
        :param action: selected action name.
        :param manager_uri: optional Manager URI selector.
        :return: matching rows.
        """
        matches = [row for row in rows if row["Action"] == action]
        if manager_uri:
            normalized = manager_uri.rstrip("/")
            matches = [
                row for row in matches
                if row["Resource"].rstrip("/") == normalized
            ]
        return matches

    def execute(self,
                action: Optional[str] = None,
                manager_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or run selected HPE iLO Manager OEM actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param manager_uri: optional Manager URI to disambiguate multiple targets.
        :param confirm: run the selected action when True.
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

        matches = self._matches(rows, action, manager_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"HPE manager action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple HPE manager-action targets found; pass --manager-uri",
            )

        row = matches[0]
        spec = _ACTION_SPECS[action]
        result = self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = "HPE manager action requires --confirm"
        return result
