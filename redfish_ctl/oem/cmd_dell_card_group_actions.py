"""Preview or run Dell card-service group membership actions.

    redfish_ctl dell-card-group-actions
    redfish_ctl dell-card-group-actions --action join --clone-configuration Enable
    redfish_ctl dell-card-group-actions --action remove-self --confirm

The command discovers ``DelliDRACCardService`` from Manager OEM Dell links and
uses the advertised action targets from that service. Group membership changes
rewrite iDRAC configuration, so selected actions preview by default and POST only
when ``--confirm`` is supplied.
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..actions.action_policy import classify
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_NAME = "DelliDRACCardService"
_DEFAULT_SERVICE_URI = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/{_SERVICE_NAME}"
)


@dataclass(frozen=True)
class _CardGroupAction:
    """Static selector metadata for one Dell card-service group action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    needs_clone_configuration: bool = False


_ACTION_SPECS = {
    "delete-group": _CardGroupAction(
        selector="delete-group",
        full_type="#DelliDRACCardService.DeleteGroup",
        action_name="DeleteGroup",
        description="delete the configured Dell iDRAC group membership",
    ),
    "join": _CardGroupAction(
        selector="join",
        full_type="#DelliDRACCardService.JoinGroup",
        action_name="JoinGroup",
        description="join a Dell iDRAC group membership",
        needs_clone_configuration=True,
    ),
    "remove-self": _CardGroupAction(
        selector="remove-self",
        full_type="#DelliDRACCardService.RemoveSelf",
        action_name="RemoveSelf",
        description="remove this iDRAC from its current group",
    ),
}


class DellCardGroupActions(RedfishManagerBase,
                           scm_type=ApiRequestType.DellCardGroupActions,
                           name="dell-card-group-actions",
                           metaclass=Singleton):
    """Discover and invoke Dell card-service group membership actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-group-actions command."""
        super(DellCardGroupActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-group-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="group action to preview or run; omit to list targets",
        )
        cmd_parser.add_argument(
            "--clone-configuration",
            dest="clone_configuration",
            default=None,
            help="CloneConfiguration payload for --action join, such as Enable",
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
            help="POST the selected group action instead of previewing it",
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
            "dell-card-group-actions",
            "command run Dell card-service group membership actions",
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
                uris.append(service_uri.rstrip("/"))
        if not uris:
            uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uris))

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
        """Build discovered group-action rows for one service URI.

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
            clone_values = self._action_values(
                service,
                spec.full_type,
                "CloneConfiguration",
            )
            if clone_values:
                parameters["CloneConfiguration"] = clone_values
            rows.append({
                "Resource": service_uri,
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Target": target,
                "Level": classify(spec.full_type).value,
                "Description": spec.description,
                "Parameters": parameters,
            })
        return rows

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell card-service group action targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DelliDRACCardService URI.
        :return: list of discovered action rows.
        """
        uris = (
            [resource_uri.rstrip("/")]
            if resource_uri else self._service_uris(do_async)
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
            matches = [
                row for row in matches if row["Resource"].rstrip("/") == wanted
            ]
        if not matches:
            raise InvalidArgument(f"Dell card group action not found: {action}")
        if len(matches) > 1:
            resources = [row["Resource"] for row in matches]
            raise InvalidArgument(
                "multiple Dell card group action targets found; "
                f"pass --resource-uri: {resources}"
            )
        return matches[0]

    @staticmethod
    def _payload_for(spec, clone_configuration):
        """Build the selected group action payload.

        :param spec: selected action metadata.
        :param clone_configuration: optional CloneConfiguration value for join.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when arguments do not match the action.
        """
        clean_clone = (clone_configuration or "").strip()
        if spec.needs_clone_configuration:
            return {"CloneConfiguration": clean_clone} if clean_clone else {}
        if clean_clone:
            raise InvalidArgument(
                "--clone-configuration is only valid with --action join"
            )
        return {}

    def execute(self,
                action: Optional[str] = None,
                clone_configuration: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell card-service group membership actions.

        :param action: optional action selector; None lists discovered targets.
        :param clone_configuration: optional CloneConfiguration payload for join.
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
            if clone_configuration:
                raise InvalidArgument(
                    "--clone-configuration is only valid with --action join"
                )
            return CommandResult({"group_action_targets": rows}, None, None, None)

        if action not in _ACTION_SPECS:
            allowed = ", ".join(sorted(_ACTION_SPECS))
            raise InvalidArgument(
                f"unsupported Dell card group action '{action}'; allowed: {allowed}"
            )
        spec = _ACTION_SPECS[action]
        row = self._resolve_row(rows, action, resource_uri=resource_uri)
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=self._payload_for(spec, clone_configuration),
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
