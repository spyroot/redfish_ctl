"""Read Redfish UpdateService capabilities and advertised actions.

Example::

    redfish_ctl update_service
    redfish_ctl update_service -f update_service.json
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class UpdateServiceQuery(RedfishManagerBase,
                         scm_type=ApiRequestType.UpdateServiceQuery,
                         name='update_service',
                         metaclass=Singleton):
    """Read the service, inventory links, push URIs, and action targets."""

    def __init__(self, *args, **kwargs):
        """Initialize the update_service command."""
        super(UpdateServiceQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only update_service subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read UpdateService capabilities and actions"
        return cmd_parser, "update_service", help_text

    @staticmethod
    def _link(data, key):
        """Return the ``@odata.id`` for a link entry, or None.

        :param data: the resource dict to read the link from.
        :param key: the link property name.
        :return: the referenced ``@odata.id`` string, or None when absent or not
            a link object.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _action_name(full_name):
        """Shorten a fully qualified action name to its trailing segment.

        :param full_name: the fully qualified action name (e.g.
            ``#UpdateService.SimpleUpdate``).
        :return: the action name after the final dot.
        """
        return full_name.lstrip("#").split(".")[-1]

    @classmethod
    def _collect_actions(cls, node):
        """Recursively collect Redfish action entries from an Actions node.

        :param node: an Actions mapping (or nested dict) to walk.
        :return: a list of action rows with Name, FullName, Target, ActionInfo,
            and Parameters.
        """
        rows = []
        if not isinstance(node, dict):
            return rows
        for key, value in node.items():
            if key.startswith("#") and isinstance(value, dict):
                target = value.get("target")
                if not target:
                    continue
                parameters = {
                    param_key.split("@", 1)[0]: param_value
                    for param_key, param_value in value.items()
                    if param_key.endswith("@Redfish.AllowableValues")
                }
                rows.append({
                    "Name": cls._action_name(key),
                    "FullName": key,
                    "Target": target,
                    "ActionInfo": value.get("@Redfish.ActionInfo"),
                    "Parameters": parameters,
                })
            elif isinstance(value, dict):
                rows.extend(cls._collect_actions(value))
        return rows

    def _update_service_uri(self, do_async):
        """Resolve the UpdateService URI from the service root, with a fallback.

        :param do_async: query the service root asynchronously.
        :return: the UpdateService ``@odata.id``, or
            ``REDFISH_API.UpdateServiceQuery`` when the root exposes no link.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        update_service = root.get("UpdateService")
        if isinstance(update_service, dict) and update_service.get("@odata.id"):
            return update_service["@odata.id"]
        return REDFISH_API.UpdateServiceQuery

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read UpdateService without invoking any update action.

        :param filename: if set, save the summarized response to this file.
        :param data_type: json or xml; serialization format passed to the query.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: read UpdateService asynchronously.
        :return: CommandResult with the summarized UpdateService (metadata,
            inventory links, push URIs, and sorted action targets).
        """
        service_uri = self._update_service_uri(do_async)
        result = self.base_query(service_uri, do_async=do_async, data_type=data_type)
        service = result.data or {}
        data = {
            "@odata.id": service.get("@odata.id", service_uri),
            "@odata.type": service.get("@odata.type"),
            "Id": service.get("Id"),
            "Name": service.get("Name"),
            "Description": service.get("Description"),
            "ServiceEnabled": service.get("ServiceEnabled"),
            "Status": service.get("Status"),
            "FirmwareInventory": self._link(service, "FirmwareInventory"),
            "SoftwareInventory": self._link(service, "SoftwareInventory"),
            "HttpPushUri": service.get("HttpPushUri"),
            "HttpPushUriOptions": service.get("HttpPushUriOptions"),
            "MultipartHttpPushUri": service.get("MultipartHttpPushUri"),
            "MultipartHttpPushUriOptions": (
                (service.get("Oem") or {})
                .get("Nvidia", {})
                .get("MultipartHttpPushUriOptions")
            ),
            "Actions": sorted(
                self._collect_actions(service.get("Actions")),
                key=lambda action: action["FullName"],
            ),
        }
        save_if_needed(filename, data)
        return CommandResult(data, None, None, None)
