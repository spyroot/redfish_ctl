"""Preview or expose Dell iSM installer media through DellLCService.

    redfish_ctl dell-lc-ism-installer
    redfish_ctl dell-lc-ism-installer --dry_run
    redfish_ctl dell-lc-ism-installer --confirm

``DellLCService.ExposeiSMInstallerToHostOS``, advertised by Dell's Lifecycle
Controller service, can expose installer media to the host operating system.
The command discovers the service through Manager OEM links and previews by
default; it POSTs only when ``--confirm`` is supplied.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_ACTION = "#DellLCService.ExposeiSMInstallerToHostOS"
_ACTION_NAME = "ExposeiSMInstallerToHostOS"
_SERVICE_FALLBACKS = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService",
    "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService",
)


class DellLcIsmInstaller(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellLcIsmInstaller,
    name="dell-lc-ism-installer",
    metaclass=Singleton,
):
    """Discover and invoke DellLCService.ExposeiSMInstallerToHostOS."""

    def __init__(self, *args, **kwargs):
        """Initialize the ``dell-lc-ism-installer`` command."""
        super(DellLcIsmInstaller, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-ism-installer`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellLCService URI when more than one target is found",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the iSM installer expose action instead of previewing it",
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
            "dell-lc-ism-installer",
            "command expose Dell iSM installer media through DellLCService",
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
        """Return the ``Links.Oem.Dell`` block from a Manager resource.

        :param manager: Redfish Manager resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating optional-resource misses.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _dell_lc_service_uris(self, do_async, resource_uri=None):
        """Return candidate DellLCService URIs in discovery-first order.

        :param do_async: run underlying Manager reads asynchronously when True.
        :param resource_uri: optional exact DellLCService URI supplied by the caller.
        :return: de-duplicated service URI list.
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
            service_uri = self._link(self._dell_oem_links(manager), "DellLCService")
            if not service_uri:
                service_uri = f"{manager_uri.rstrip('/')}/Oem/Dell/DellLCService"
            if service_uri not in uris:
                uris.append(service_uri)
        if uris:
            return uris
        for fallback in _SERVICE_FALLBACKS:
            if fallback not in uris:
                uris.append(fallback)
        return uris

    def _discover_rows(self, do_async, resource_uri=None):
        """Return discovered iSM installer action rows.

        :param do_async: run Redfish queries asynchronously when True.
        :param resource_uri: optional exact DellLCService URI supplied by the caller.
        :return: list of action target rows.
        """
        rows = []
        for service_uri in self._dell_lc_service_uris(do_async, resource_uri):
            resource = self._get(service_uri, do_async)
            target = self._flatten_action_targets(resource).get(_ACTION)
            if target:
                rows.append({
                    "Action": _ACTION,
                    "Resource": service_uri,
                    "Target": target,
                    "Payload": {},
                })
        return rows

    def execute(
            self,
            resource_uri: Optional[str] = None,
            confirm: Optional[bool] = False,
            dry_run: Optional[bool] = False,
            filename: Optional[str] = None,
            data_type: Optional[str] = "json",
            verbose: Optional[bool] = False,
            do_async: Optional[bool] = False,
            **kwargs) -> CommandResult:
        """Preview or invoke DellLCService.ExposeiSMInstallerToHostOS.

        :param resource_uri: optional exact DellLCService URI to inspect.
        :param confirm: POST the action when True; otherwise return a dry-run preview.
        :param dry_run: force a no-POST preview even when confirmation is supplied.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying GET/POST on the async path when True.
        :return: CommandResult with preview, execution result, or missing-action error.
        """
        rows = self._discover_rows(bool(do_async), resource_uri)
        if not rows:
            return CommandResult(
                {"action": _ACTION, "available": []},
                None,
                None,
                f"action '{_ACTION}' not found on DellLCService",
            )
        if len(rows) > 1:
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple DellLCService iSM installer targets found; pass --resource-uri",
            )

        row = rows[0]
        return self.invoke_action(
            row["Resource"],
            _ACTION_NAME,
            payload={},
            full_action_type=_ACTION,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
