"""Preview or fire HPE iLO diagnostic/test Redfish actions.

    redfish_ctl hpe-test-actions
    redfish_ctl hpe-test-actions --action snmp-alert
    redfish_ctl hpe-test-actions --action syslog-alert --confirm

The command discovers HPE OEM test-action targets from the live Redfish tree and
dry-runs by default, because these actions can send test SNMP, mail, syslog, or
directory-auth traffic outside the BMC. Use ``--confirm`` to send one selected
test action. No target URL is hardcoded.

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
class _HpeTestActionSpec:
    """Static selector metadata for one HPE test action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    resource_kind: str


_ACTION_SPECS = {
    "directory-start": _HpeTestActionSpec(
        selector="directory-start",
        full_type="#HpeDirectoryTest.StartTest",
        action_name="StartTest",
        description="start the configured directory authentication test",
        resource_kind="directory-test",
    ),
    "directory-stop": _HpeTestActionSpec(
        selector="directory-stop",
        full_type="#HpeDirectoryTest.StopTest",
        action_name="StopTest",
        description="stop the configured directory authentication test",
        resource_kind="directory-test",
    ),
    "snmp-alert": _HpeTestActionSpec(
        selector="snmp-alert",
        full_type="#HpeiLOSnmpService.SendSNMPTestAlert",
        action_name="SendSNMPTestAlert",
        description="send an SNMP test alert through the configured SNMP service",
        resource_kind="snmp-service",
    ),
    "mail-alert": _HpeTestActionSpec(
        selector="mail-alert",
        full_type="#HpeiLOManagerNetworkService.SendTestAlertMail",
        action_name="SendTestAlertMail",
        description="send a test alert-mail message through ManagerNetworkProtocol",
        resource_kind="manager-network",
    ),
    "syslog-alert": _HpeTestActionSpec(
        selector="syslog-alert",
        full_type="#HpeiLOManagerNetworkService.SendTestSyslog",
        action_name="SendTestSyslog",
        description="send a test syslog message through ManagerNetworkProtocol",
        resource_kind="manager-network",
    ),
}


class HpeTestActions(IDracManager,
                     scm_type=ApiRequestType.HpeTestActions,
                     name="hpe-test-actions",
                     metaclass=Singleton):
    """Discover and invoke HPE iLO diagnostic/test actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the hpe-test-actions command."""
        super(HpeTestActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``hpe-test-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="HPE test action to preview or send; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific resource URI when more than one target advertises the action",
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
        return cmd_parser, "hpe-test-actions", "command run HPE iLO test actions"

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
    def _hpe(data):
        """Return the ``Oem.Hpe`` extension block from a resource.

        :param data: Redfish resource body.
        :return: HPE OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        hpe = oem.get("Hpe") if isinstance(oem, dict) else None
        return hpe if isinstance(hpe, dict) else {}

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

    def _account_service_uri(self, do_async):
        """Resolve AccountService, with the standard path as fallback.

        :param do_async: run the service-root query asynchronously when True.
        :return: AccountService URI.
        """
        root = self._get(RedfishApi.Version, do_async)
        return self._link(root, "AccountService") or f"{RedfishApi.Version}/AccountService"

    def _directory_test_uris(self, do_async):
        """Return HPE DirectoryTest resource URIs.

        :param do_async: run the AccountService query asynchronously when True.
        :return: list of directory-test resource URIs.
        """
        account_uri = self._account_service_uri(do_async)
        account = self._get(account_uri, do_async)
        hpe = self._hpe(account)
        directory_settings = hpe.get("DirectorySettings")
        directory_uri = self._link(hpe, "DirectoryTest")
        if not directory_uri and isinstance(directory_settings, dict):
            directory_uri = self._link(directory_settings, "DirectoryTest")
        if not directory_uri:
            directory_uri = f"{account_uri.rstrip('/')}/DirectoryTest"
        return [directory_uri]

    def _network_protocol_uris(self, do_async):
        """Return ManagerNetworkProtocol resource URIs for every manager.

        :param do_async: run manager queries asynchronously when True.
        :return: list of ManagerNetworkProtocol URIs.
        """
        uris = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            network_uri = self._link(manager, "NetworkProtocol")
            if network_uri:
                uris.append(network_uri)
        return uris

    def _snmp_service_uris(self, do_async):
        """Return HPE SNMP service URIs from ManagerNetworkProtocol links.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of SNMP service URIs.
        """
        uris = []
        for network_uri in self._network_protocol_uris(do_async):
            network = self._get(network_uri, do_async)
            hpe = self._hpe(network)
            links = hpe.get("Links") if isinstance(hpe, dict) else None
            snmp_uri = self._link(links or {}, "SNMPService")
            if not snmp_uri:
                manager_uri = network_uri.rsplit("/NetworkProtocol", 1)[0]
                snmp_uri = f"{manager_uri}/SnmpService"
            uris.append(snmp_uri)
        return uris

    def _resource_uris_for(self, spec, do_async):
        """Return candidate resource URIs for an HPE test-action selector.

        :param spec: HPE test-action selector metadata.
        :param do_async: run underlying queries asynchronously when True.
        :return: list of candidate resource URIs.
        """
        if spec.resource_kind == "directory-test":
            return self._directory_test_uris(do_async)
        if spec.resource_kind == "snmp-service":
            return self._snmp_service_uris(do_async)
        if spec.resource_kind == "manager-network":
            return self._network_protocol_uris(do_async)
        return []

    def _target_for(self, resource_uri, spec, do_async):
        """Return the advertised target URI for a test action on a resource.

        :param resource_uri: candidate resource URI.
        :param spec: HPE test-action selector metadata.
        :param do_async: run the resource query asynchronously when True.
        :return: action target URI, or None when the resource lacks that action.
        """
        resource = self._get(resource_uri, do_async)
        targets = self._flatten_action_targets(resource)
        return targets.get(spec.full_type)

    def _discover_rows(self, do_async):
        """Discover available HPE test actions from linked resources.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available test-action rows.
        """
        rows = []
        for spec in _ACTION_SPECS.values():
            for resource_uri in self._resource_uris_for(spec, do_async):
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

        :param rows: discovered HPE test-action rows.
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
        """List or send HPE iLO diagnostic/test actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional resource URI to disambiguate multiple targets.
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
                f"HPE test action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple HPE test-action targets found; pass --resource-uri",
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
            result.data["blocked"] = "HPE test action requires --confirm"
        return result
