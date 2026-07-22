"""Read Dell LC SupportAssist status values exposed as Redfish actions.

    redfish_ctl dell-lc-supportassist-status
    redfish_ctl dell-lc-supportassist-status --action eula-status
    redfish_ctl dell-lc-supportassist-status --action auto-collect-schedule --dry_run

The command discovers DellLCService through Manager OEM links and invokes only
the no-payload status actions listed below. It does not configure
SupportAssist, accept an EULA, upload collections, export reports, or touch any
network share.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


@dataclass(frozen=True)
class _SupportAssistStatusSpec:
    """Static selector metadata for one Dell LC SupportAssist status action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "auto-collect-schedule": _SupportAssistStatusSpec(
        selector="auto-collect-schedule",
        full_type="#DellLCService.SupportAssistGetAutoCollectSchedule",
        action_name="SupportAssistGetAutoCollectSchedule",
        description="read the configured SupportAssist auto-collect schedule",
    ),
    "eula-status": _SupportAssistStatusSpec(
        selector="eula-status",
        full_type="#DellLCService.SupportAssistGetEULAStatus",
        action_name="SupportAssistGetEULAStatus",
        description="read the SupportAssist EULA acceptance status",
    ),
}


class DellLcSupportAssistStatus(
    IDracManager,
    scm_type=ApiRequestType.DellLcSupportAssistStatus,
    name="dell-lc-supportassist-status",
    metaclass=Singleton,
):
    """Discover and invoke Dell LC SupportAssist status actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-supportassist-status command."""
        super(DellLcSupportAssistStatus, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-supportassist-status`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="SupportAssist status action to invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellLCService URI when more than one target is found",
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
            "dell-lc-supportassist-status",
            "command read Dell LC SupportAssist status actions",
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
    def _dell_oem_links(data):
        """Return the ``Links.Oem.Dell`` block from a Manager resource.

        :param data: Redfish Manager resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

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

    def _dell_lc_service_uris(self, do_async):
        """Return DellLCService URIs discovered from Manager OEM links.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of DellLCService resource URIs.
        """
        uris = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            dell = self._dell_oem_links(manager)
            service_uri = self._link(dell, "DellLCService")
            if not service_uri:
                service_uri = f"{manager_uri.rstrip('/')}/Oem/Dell/DellLCService"
            if service_uri not in uris:
                uris.append(service_uri)
        return uris

    def _target_for(self, resource_uri, spec, do_async):
        """Return the advertised target URI for a SupportAssist status action.

        :param resource_uri: DellLCService resource URI.
        :param spec: SupportAssist status selector metadata.
        :param do_async: run the query asynchronously when True.
        :return: action target URI, or None when the resource lacks the action.
        """
        resource = self._get(resource_uri, do_async)
        targets = self._flatten_action_targets(resource)
        return targets.get(spec.full_type)

    def _discover_rows(self, do_async):
        """Discover available Dell LC SupportAssist status actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available status-action rows.
        """
        rows = []
        for spec in _ACTION_SPECS.values():
            for resource_uri in self._dell_lc_service_uris(do_async):
                target = self._target_for(resource_uri, spec, do_async)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": resource_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, resource_uri):
        """Filter discovered rows by action selector and optional resource URI.

        :param rows: discovered status-action rows.
        :param action: selected action name.
        :param resource_uri: optional DellLCService URI selector.
        :return: matching rows.
        """
        matches = [row for row in rows if row["Action"] == action]
        if resource_uri:
            normalized = resource_uri.rstrip("/")
            matches = [
                row for row in matches
                if row["Resource"].rstrip("/") == normalized
            ]
        return matches

    def execute(self,
                action: Optional[str] = None,
                resource_uri: Optional[str] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell LC SupportAssist status actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional DellLCService URI to disambiguate targets.
        :param dry_run: force preview mode without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = self._matches(rows, action, resource_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell LC SupportAssist status action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell LC SupportAssist targets found; pass --resource-uri",
            )

        row = matches[0]
        spec = _ACTION_SPECS[action]
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=True,
        )
