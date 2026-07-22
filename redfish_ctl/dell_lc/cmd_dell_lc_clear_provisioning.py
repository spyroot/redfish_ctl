"""Preview or clear the Dell Lifecycle Controller provisioning server config.

    redfish_ctl dell-lc-clear-provisioning
    redfish_ctl dell-lc-clear-provisioning --confirm

The command resolves ``#DellLCService.ClearProvisioningServer`` from the Dell
Lifecycle Controller service. Clearing the provisioning server changes BMC
configuration, so the command previews by default and only POSTs when
``--confirm`` is provided.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton

_CLEAR_PROVISIONING_ACTION = "#DellLCService.ClearProvisioningServer"
_SERVICE_NAME = "DellLCService"
_DEFAULT_SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
_LEGACY_SERVICE_URI = (
    "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService"
)


class DellLcClearProvisioningServer(
        IDracManager,
        scm_type=ApiRequestType.DellLcClearProvisioningServer,
        name="dell-lc-clear-provisioning",
        metaclass=Singleton):
    """Discover and invoke DellLCService.ClearProvisioningServer."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-clear-provisioning command."""
        super(DellLcClearProvisioningServer, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-clear-provisioning`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
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
            help="POST the clear-provisioning action instead of previewing it",
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
            "dell-lc-clear-provisioning",
            "command clear Dell Lifecycle Controller provisioning server config",
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

        :param do_async: issue Manager queries on the async path when True.
        :return: de-duplicated candidate service URIs.
        """
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
        uris.extend([_DEFAULT_SERVICE_URI, _LEGACY_SERVICE_URI])
        return list(dict.fromkeys(uri for uri in uris if uri))

    def _metadata(self, do_async, resource_uri=None):
        """Return the discovered clear-provisioning target metadata.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: CommandResult containing target metadata or an error.
        """
        checked = []
        uris = [resource_uri] if resource_uri else self._service_uris(do_async)
        for uri in dict.fromkeys(uri for uri in uris if uri):
            service = self._get(uri, do_async)
            if not service:
                checked.append(uri)
                continue
            target = self._flatten_action_targets(service).get(
                _CLEAR_PROVISIONING_ACTION
            )
            actions = self.discover_redfish_actions(self, service)
            if target:
                return CommandResult(
                    {
                        "lc_service": uri,
                        "action": _CLEAR_PROVISIONING_ACTION,
                        "target": target,
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(uri)
        return CommandResult(
            {
                "action": _CLEAR_PROVISIONING_ACTION,
                "checked": checked,
            },
            None,
            None,
            f"action '{_CLEAR_PROVISIONING_ACTION}' not found",
        )

    def execute(self,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or invoke DellLCService.ClearProvisioningServer.

        :param resource_uri: optional DellLCService URI to inspect directly.
        :param confirm: POST the action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue Redfish reads/POST on the async path when True.
        :return: CommandResult with a preview, execution result, or error.
        """
        metadata = self._metadata(bool(do_async), resource_uri)
        if metadata.error is not None:
            return metadata

        result = self.invoke_action(
            metadata.data["lc_service"],
            "ClearProvisioningServer",
            payload={},
            full_action_type=_CLEAR_PROVISIONING_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if result.error is None and isinstance(result.data, dict):
            result.data.setdefault("lc_service", metadata.data["lc_service"])
        return result
