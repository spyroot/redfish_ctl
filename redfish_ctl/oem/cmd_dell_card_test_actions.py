"""Preview or fire Dell iDRAC card diagnostic/test Redfish actions.

    redfish_ctl dell-card-test-actions
    redfish_ctl dell-card-test-actions --action snmp-trap
    redfish_ctl dell-card-test-actions --action rsyslog --confirm

The command discovers Dell OEM test-action targets from
``Links.Oem.Dell.DelliDRACCardService`` on each Manager resource. It dry-runs by
default because these actions can send test email, SNMP, or rsyslog traffic
outside the BMC. Use ``--confirm`` to send one selected test action.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton


@dataclass(frozen=True)
class _DellCardTestActionSpec:
    """Static selector metadata for one Dell card service test action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "email-alert": _DellCardTestActionSpec(
        selector="email-alert",
        full_type="#DelliDRACCardService.SendTestEmailAlert",
        action_name="SendTestEmailAlert",
        description="send a test email alert through the configured iDRAC service",
    ),
    "snmp-trap": _DellCardTestActionSpec(
        selector="snmp-trap",
        full_type="#DelliDRACCardService.SendTestSNMPTrap",
        action_name="SendTestSNMPTrap",
        description="send a test SNMP trap through the configured iDRAC service",
    ),
    "rsyslog": _DellCardTestActionSpec(
        selector="rsyslog",
        full_type="#DelliDRACCardService.TestRsyslogServerConnection",
        action_name="TestRsyslogServerConnection",
        description="test the configured remote syslog server connection",
    ),
}


class DellCardTestActions(IDracManager,
                          scm_type=ApiRequestType.DellCardTestActions,
                          name="dell-card-test-actions",
                          metaclass=Singleton):
    """Discover and invoke Dell iDRAC card diagnostic/test actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-test-actions command."""
        super(DellCardTestActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-test-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="Dell iDRAC card test action to preview or send; omit to list",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DelliDRACCardService URI when more than one target exists",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="send the selected test action instead of dry-running it",
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
            "dell-card-test-actions",
            "command run Dell iDRAC card test actions",
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
    def _oem_dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _links_oem_dell(data):
        """Return the ``Links.Oem.Dell`` block from a resource.

        :param data: Redfish resource body.
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

    def _card_service_uris(self, do_async):
        """Return DelliDRACCardService resource URIs for every manager.

        :param do_async: run manager queries asynchronously when True.
        :return: list of DelliDRACCardService URIs.
        """
        uris = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            links_dell = self._links_oem_dell(manager)
            service_uri = self._link(links_dell, "DelliDRACCardService")
            if not service_uri:
                oem_dell = self._oem_dell(manager)
                service_uri = self._link(oem_dell, "DelliDRACCardService")
            if not service_uri:
                service_uri = f"{manager_uri.rstrip('/')}/Oem/Dell/DelliDRACCardService"
            uris.append(service_uri)
        return uris

    def _target_for(self, service_uri, spec, do_async):
        """Return the advertised target URI for a Dell card test action.

        :param service_uri: candidate DelliDRACCardService URI.
        :param spec: Dell card test-action selector metadata.
        :param do_async: run the service query asynchronously when True.
        :return: action target URI, or None when the resource lacks that action.
        """
        service = self._get(service_uri, do_async)
        targets = self._flatten_action_targets(service)
        return targets.get(spec.full_type)

    def _discover_rows(self, do_async):
        """Discover available Dell card test actions from linked services.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available test-action rows.
        """
        rows = []
        for service_uri in self._card_service_uris(do_async):
            for spec in _ACTION_SPECS.values():
                target = self._target_for(service_uri, spec, do_async)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": service_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, resource_uri):
        """Filter discovered rows by action selector and optional resource URI.

        :param rows: discovered Dell card test-action rows.
        :param action: selected action name.
        :param resource_uri: optional resource URI selector.
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
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or send Dell iDRAC card diagnostic/test actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional service URI to disambiguate multiple targets.
        :param confirm: send the selected test action when True.
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

        matches = self._matches(rows, action, resource_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell iDRAC card test action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell iDRAC card test-action targets found; "
                "pass --resource-uri",
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
            result.data["blocked"] = "Dell iDRAC card test action requires --confirm"
        return result
