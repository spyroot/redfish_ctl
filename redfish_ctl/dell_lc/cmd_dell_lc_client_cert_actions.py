"""Preview or run Dell LC client-certificate maintenance actions.

    redfish_ctl dell-lc-client-cert-actions
    redfish_ctl dell-lc-client-cert-actions --mode download-client-certs
    redfish_ctl dell-lc-client-cert-actions --mode delete-client-certs --confirm

The command resolves client-certificate actions advertised by DellLCService.
These actions change provisioning trust material, so the command lists targets
when ``--mode`` is omitted and otherwise dry-runs unless ``--confirm`` is
provided.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_SERVICE_NAME = "DellLCService"
_SERVICE_FALLBACKS = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService",
    "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService",
)

_ACTION_SPECS = {
    "delete-client-certs": {
        "short": "DeleteAutoDiscoveryClientCerts",
        "full": "#DellLCService.DeleteAutoDiscoveryClientCerts",
    },
    "delete-server-key": {
        "short": "DeleteAutoDiscoveryServerPublicKey",
        "full": "#DellLCService.DeleteAutoDiscoveryServerPublicKey",
    },
    "download-client-certs": {
        "short": "DownloadClientCerts",
        "full": "#DellLCService.DownloadClientCerts",
    },
}


class DellLcClientCertActions(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellLcClientCertActions,
    name="dell-lc-client-cert-actions",
    metaclass=Singleton,
):
    """List or invoke DellLCService client-certificate actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the ``dell-lc-client-cert-actions`` command."""
        super(DellLcClientCertActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-client-cert-actions`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--mode",
            choices=tuple(_ACTION_SPECS),
            default=None,
            help="Dell LC certificate action to run; omit to list targets",
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
            help="POST the selected action instead of previewing it",
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
            "dell-lc-client-cert-actions",
            "command manage Dell LC auto-discovery certificate actions",
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

    def _service_uris(self, do_async, resource_uri=None):
        """Return candidate DellLCService URIs in discovery-first order.

        :param do_async: issue Manager queries on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: de-duplicated candidate service URIs.
        """
        if resource_uri:
            return [resource_uri.rstrip("/")]

        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._dell_oem_links(manager),
                _SERVICE_NAME,
            )
            if service_uri:
                uris.append(service_uri)
            uris.append(manager_uri.rstrip("/") + f"/Oem/Dell/{_SERVICE_NAME}")
        uris.extend(_SERVICE_FALLBACKS)
        return list(dict.fromkeys(uri for uri in uris if uri))

    @staticmethod
    def _action_row(actions, action_name, full_action):
        """Return a normalized row for one discovered action.

        :param actions: mapping returned by ``discover_redfish_actions``.
        :param action_name: short Redfish action name.
        :param full_action: full Redfish action name.
        :return: target/action summary dict, or None when absent.
        """
        action = actions.get(action_name)
        target = getattr(action, "target", None)
        if not target:
            return None
        return {
            "action": full_action,
            "target": target,
            "payload": {},
        }

    def _metadata(self, do_async, resource_uri=None):
        """Return discovered Dell LC certificate action metadata.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: CommandResult containing target metadata or an error.
        """
        checked = []
        for uri in self._service_uris(do_async, resource_uri):
            service = self._get(uri, do_async)
            if not service:
                checked.append(uri)
                continue

            actions = self.discover_redfish_actions(self, service)
            discovered = {}
            for mode, spec in _ACTION_SPECS.items():
                row = self._action_row(
                    actions,
                    spec["short"],
                    spec["full"],
                )
                if row is not None:
                    discovered[mode] = row
            if discovered:
                return CommandResult(
                    {
                        "lc_service": uri,
                        "actions": discovered,
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(uri)

        wanted = ", ".join(spec["full"] for spec in _ACTION_SPECS.values())
        return CommandResult(
            {"actions": sorted(_ACTION_SPECS), "checked": checked},
            None,
            None,
            f"actions not found: {wanted}",
        )

    def execute(
            self,
            mode: Optional[str] = None,
            resource_uri: Optional[str] = None,
            confirm: Optional[bool] = False,
            dry_run: Optional[bool] = False,
            filename: Optional[str] = None,
            data_type: Optional[str] = "json",
            verbose: Optional[bool] = False,
            do_async: Optional[bool] = False,
            **kwargs) -> CommandResult:
        """List, preview, or invoke DellLCService certificate actions.

        :param mode: selected action mode, or None to list discovered actions.
        :param resource_uri: optional DellLCService URI to inspect directly.
        :param confirm: POST the action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue Redfish reads/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        metadata = self._metadata(bool(do_async), resource_uri)
        if metadata.error is not None or mode is None:
            return metadata

        spec = _ACTION_SPECS.get(mode)
        if spec is None:
            valid = ", ".join(sorted(_ACTION_SPECS))
            return CommandResult(
                {"mode": mode, "valid_modes": sorted(_ACTION_SPECS)},
                None,
                None,
                f"invalid mode '{mode}'; expected one of: {valid}",
            )
        if mode not in metadata.data["actions"]:
            return CommandResult(
                {
                    "mode": mode,
                    "available_modes": sorted(metadata.data["actions"]),
                },
                None,
                None,
                f"action '{spec['full']}' not found on DellLCService",
            )

        result = self.invoke_action(
            metadata.data["lc_service"],
            spec["short"],
            payload={},
            full_action_type=spec["full"],
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if result.error is None and isinstance(result.data, dict):
            result.data.setdefault("lc_service", metadata.data["lc_service"])
            result.data.setdefault("mode", mode)
        return result
