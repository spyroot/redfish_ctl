"""Preview or run Dell card-service key-management actions.

    redfish_ctl dell-card-key-management
    redfish_ctl dell-card-key-management --action disable-sekm
    redfish_ctl dell-card-key-management --action rekey --mode SEKM --confirm

The command discovers ``DelliDRACCardService`` from Manager OEM links and
resolves the advertised key-management action targets from that resource.
Selected actions preview by default because they rewrite BMC security
configuration. Use ``--confirm`` to POST one selected action.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_NAME = "DelliDRACCardService"
_DEFAULT_SERVICE_URI = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/{_SERVICE_NAME}"
)


@dataclass(frozen=True)
class _CardKeyAction:
    """Static selector metadata for one Dell card-service action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    needs_mode: bool = False


_ACTION_SPECS = {
    "disable-ilkm": _CardKeyAction(
        selector="disable-ilkm",
        full_type="#DelliDRACCardService.DisableiLKM",
        action_name="DisableiLKM",
        description="disable local key management",
    ),
    "disable-sekm": _CardKeyAction(
        selector="disable-sekm",
        full_type="#DelliDRACCardService.DisableSEKM",
        action_name="DisableSEKM",
        description="disable secure enterprise key management",
    ),
    "enable-ilkm": _CardKeyAction(
        selector="enable-ilkm",
        full_type="#DelliDRACCardService.EnableiLKM",
        action_name="EnableiLKM",
        description="enable local key management",
    ),
    "enable-sekm": _CardKeyAction(
        selector="enable-sekm",
        full_type="#DelliDRACCardService.EnableSEKM",
        action_name="EnableSEKM",
        description="enable secure enterprise key management",
    ),
    "rekey": _CardKeyAction(
        selector="rekey",
        full_type="#DelliDRACCardService.Rekey",
        action_name="Rekey",
        description="rekey the selected Dell key-management mode",
        needs_mode=True,
    ),
    "transition-ilkm-to-sekm": _CardKeyAction(
        selector="transition-ilkm-to-sekm",
        full_type="#DelliDRACCardService.iLKMToSEKMTransition",
        action_name="iLKMToSEKMTransition",
        description="transition from local to secure enterprise key management",
    ),
}


class DellCardKeyManagement(IDracManager,
                            scm_type=ApiRequestType.DellCardKeyManagement,
                            name="dell-card-key-management",
                            metaclass=Singleton):
    """Discover and invoke Dell card-service key-management actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-key-management command."""
        super(DellCardKeyManagement, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-key-management`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="key-management action to preview or run; omit to list targets",
        )
        cmd_parser.add_argument(
            "--mode",
            default=None,
            help="Mode payload for --action rekey; usually SEKM or iLKM",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DelliDRACCardService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected key-management action instead of previewing it",
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
            "dell-card-key-management",
            "command run Dell card-service key-management actions",
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
    def _dell_oem(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _dell_oem_links(data):
        """Return the ``Links.Oem.Dell`` extension block from a resource.

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
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri.rstrip("/"), do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DelliDRACCardService resource URIs.

        :param do_async: issue Manager queries on the async path when True.
        :return: ordered list of candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(self._dell_oem_links(manager), _SERVICE_NAME)
            if not service_uri:
                service_uri = self._link(self._dell_oem(manager), _SERVICE_NAME)
            if service_uri:
                uris.append(service_uri)
        if not uris:
            uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uri.rstrip("/") for uri in uris))

    @staticmethod
    def _action_values(service, full_type, parameter):
        """Return inline allowable values for one action parameter.

        :param service: DelliDRACCardService resource body.
        :param full_type: full ``#Type.Action`` action name.
        :param parameter: Redfish action parameter name.
        :return: list of allowable values advertised by the service.
        """
        actions = service.get("Actions") if isinstance(service, dict) else None
        action = actions.get(full_type) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return []
        values = action.get(f"{parameter}@Redfish.AllowableValues") or []
        return sorted(values) if isinstance(values, list) else []

    def _rows_for(self, service_uri, do_async):
        """Build discovered key-management rows for one service URI.

        :param service_uri: candidate DelliDRACCardService URI.
        :param do_async: issue the service query on the async path when True.
        :return: list of discovered target rows.
        """
        service = self._get(service_uri, do_async)
        if not service:
            return []
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _ACTION_SPECS.values():
            target = targets.get(spec.full_type)
            if not target:
                continue
            parameters = {}
            mode_values = self._action_values(service, spec.full_type, "Mode")
            if mode_values:
                parameters["Mode"] = mode_values
            rows.append({
                "Resource": service_uri,
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Target": target,
                "Description": spec.description,
                "Parameters": parameters,
            })
        return rows

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell card-service key-management targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DelliDRACCardService URI.
        :return: list of discovered action rows.
        """
        uris = [resource_uri.rstrip("/")] if resource_uri else self._service_uris(
            do_async
        )
        rows = []
        for uri in dict.fromkeys(uris):
            rows.extend(self._rows_for(uri, do_async))
        return rows

    @staticmethod
    def _resolve_row(rows, action, resource_uri=None):
        """Resolve one selected action row.

        :param rows: discovered action rows.
        :param action: selected action name.
        :param resource_uri: optional DelliDRACCardService URI selector.
        :return: matching row.
        :raises InvalidArgument: when selection is absent or ambiguous.
        """
        matches = [row for row in rows if row["Action"] == action]
        if resource_uri:
            wanted = resource_uri.rstrip("/")
            matches = [row for row in matches if row["Resource"].rstrip("/") == wanted]
        if not matches:
            raise InvalidArgument(f"Dell card key-management action not found: {action}")
        if len(matches) > 1:
            resources = [row["Resource"] for row in matches]
            raise InvalidArgument(
                "multiple Dell card key-management targets found; "
                f"pass --resource-uri: {resources}"
            )
        return matches[0]

    @staticmethod
    def _payload_for(spec, mode):
        """Build the selected key-management payload.

        :param spec: selected action metadata.
        :param mode: optional Mode argument for Rekey.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when arguments do not match the action.
        """
        clean_mode = (mode or "").strip()
        if spec.needs_mode:
            if not clean_mode:
                raise InvalidArgument("--mode is required for --action rekey")
            return {"Mode": clean_mode}
        if clean_mode:
            raise InvalidArgument("--mode is only valid with --action rekey")
        return {}

    def execute(self,
                action: Optional[str] = None,
                mode: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell card-service key-management actions.

        :param action: optional action selector; None lists discovered targets.
        :param mode: ``Mode`` payload for ``--action rekey``.
        :param resource_uri: optional DelliDRACCardService URI selector.
        :param confirm: authorize a POST. Without this the action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        :raises InvalidArgument: when selection or payload arguments are invalid.
        """
        rows = self._discover_rows(bool(do_async), resource_uri=resource_uri)
        if action is None:
            if mode:
                raise InvalidArgument("--mode is only valid with --action rekey")
            return CommandResult({"key_management_targets": rows}, None, None, None)

        spec = _ACTION_SPECS[action]
        row = self._resolve_row(rows, action, resource_uri=resource_uri)
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=self._payload_for(spec, mode),
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
