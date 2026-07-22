"""Preview or update Dell Lifecycle Controller OS application health data.

    redfish_ctl dell-lc-os-health-update
    redfish_ctl dell-lc-os-health-update --update-type Automatic
    redfish_ctl dell-lc-os-health-update --update-type Automatic --confirm

The command resolves ``#DellLCService.UpdateOSAppHealthData`` from the Dell
Lifecycle Controller service. With no update type it lists the discovered action
target. With an update type it previews by default; ``--confirm`` is required
before the POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton

_ACTION_TYPE = "#DellLCService.UpdateOSAppHealthData"
_ACTION_NAME = "UpdateOSAppHealthData"
_SERVICE_NAME = "DellLCService"
_DEFAULT_SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
_DEFAULT_UPDATE_TYPES = ("Automatic",)


class DellLcOsHealthUpdate(IDracManager,
                           scm_type=ApiRequestType.DellLcOsHealthUpdate,
                           name="dell-lc-os-health-update",
                           metaclass=Singleton):
    """Discover and invoke DellLCService.UpdateOSAppHealthData."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-os-health-update command."""
        super(DellLcOsHealthUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-os-health-update`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--update-type",
            dest="update_type",
            default=None,
            help="DellLCService.UpdateOSAppHealthData UpdateType; omit to list target",
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
            help="POST the OS health update instead of previewing it",
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
            "dell-lc-os-health-update",
            "command update Dell Lifecycle Controller OS application health data",
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
    def _allowed_update_types(service, actions):
        """Return advertised ``UpdateType`` values for the OS-health action.

        :param service: DellLCService resource body.
        :param actions: discovered Redfish action map for ``service``.
        :return: sorted list of allowed update type strings.
        """
        action = actions.get(_ACTION_NAME)
        allowed = tuple(getattr(action, "args", {}).get("UpdateType", ()) or ())
        if not allowed:
            raw_actions = service.get("Actions") if isinstance(service, dict) else None
            raw_action = (
                raw_actions.get(_ACTION_TYPE)
                if isinstance(raw_actions, dict)
                else None
            )
            if isinstance(raw_action, dict):
                allowed = tuple(
                    raw_action.get("UpdateType@Redfish.AllowableValues", ()) or ()
                )
        if not allowed:
            allowed = _DEFAULT_UPDATE_TYPES
        return sorted(allowed)

    def _row_for(self, resource_uri, do_async):
        """Build a discovered UpdateOSAppHealthData row for one service URI.

        :param resource_uri: candidate DellLCService URI.
        :param do_async: issue the service query on the async path when True.
        :return: discovered row, or None when the action is absent.
        """
        service = self._get(resource_uri, do_async)
        if not service:
            return None
        target = self._flatten_action_targets(service).get(_ACTION_TYPE)
        if not target:
            return None
        actions = self.discover_redfish_actions(self, service)
        return {
            "Resource": resource_uri,
            "Action": _ACTION_TYPE,
            "Target": target,
            "AllowedUpdateTypes": self._allowed_update_types(service, actions),
        }

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell LC OS-health update targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: list of discovered target rows.
        """
        uris = [resource_uri] if resource_uri else self._service_uris(do_async)
        rows = []
        for uri in dict.fromkeys(uris):
            row = self._row_for(uri, do_async)
            if row:
                rows.append(row)
        return rows

    def execute(self,
                update_type: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke DellLCService.UpdateOSAppHealthData.

        :param update_type: optional ``UpdateType`` value; omitted means list metadata.
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
        if update_type is None:
            return CommandResult({"os_health_update_targets": rows}, None, None, None)

        if not rows:
            return CommandResult(
                {"action": _ACTION_TYPE, "available": []},
                None,
                None,
                "Dell LC OS health update action not found",
            )
        if len(rows) > 1:
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple Dell LC OS health update targets found; pass --resource-uri",
            )

        return self.invoke_action(
            rows[0]["Resource"],
            _ACTION_NAME,
            payload={"UpdateType": update_type},
            full_action_type=_ACTION_TYPE,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
