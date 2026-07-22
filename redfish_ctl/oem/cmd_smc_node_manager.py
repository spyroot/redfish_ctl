"""Supermicro Node Manager policy actions.

    redfish_ctl smc-clear-policies
    redfish_ctl smc-clear-policies --node-manager "Node Manager"
    redfish_ctl smc-clear-policies --node-manager /redfish/v1/Systems/1/SmcNodeManager --confirm

The command discovers the Supermicro OEM ``SmcNodeManager`` link from each
ComputerSystem resource and keeps only Node Manager resources that advertise
``#SmcNodeManager.ClearAllPolicies``. Clearing policies rewrites BMC policy
configuration, so the action is guarded: without ``--confirm`` it resolves and
previews the POST target but does not mutate.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_CLEAR_POLICIES_ACTION = "#SmcNodeManager.ClearAllPolicies"


class SmcNodeManagerClearPolicies(
    IDracManager,
    scm_type=ApiRequestType.SmcNodeManagerClearPolicies,
    name="smc-clear-policies",
    metaclass=Singleton,
):
    """Clear Supermicro Node Manager policies through the advertised action."""

    def __init__(self, *args, **kwargs):
        """Initialize the smc-clear-policies command."""
        super(SmcNodeManagerClearPolicies, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``smc-clear-policies`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--node-manager",
            required=False,
            dest="node_manager",
            type=str,
            default=None,
            help=(
                "Node Manager Id or full URI to clear; omit to list capable "
                "Node Manager resources without mutating"
            ),
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the ClearAllPolicies POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing",
        )
        return (
            cmd_parser,
            "smc-clear-policies",
            "command clear Supermicro Node Manager policies",
        )

    @staticmethod
    def _odata_id(data):
        """Return the Redfish link URI from a string or link object.

        :param data: candidate Redfish link value.
        :return: linked ``@odata.id`` URI, or None when absent.
        """
        if isinstance(data, str):
            return data
        if isinstance(data, dict) and isinstance(data.get("@odata.id"), str):
            return data["@odata.id"]
        return None

    @staticmethod
    def _node_manager_link(system):
        """Find the OEM ``SmcNodeManager`` link on a ComputerSystem resource.

        :param system: a ComputerSystem payload.
        :return: the linked Node Manager URI, or None when absent.
        """
        if not isinstance(system, dict):
            return None
        direct = SmcNodeManagerClearPolicies._odata_id(system.get("SmcNodeManager"))
        if direct:
            return direct
        oem = system.get("Oem")
        if not isinstance(oem, dict):
            return None
        for vendor_payload in oem.values():
            if not isinstance(vendor_payload, dict):
                continue
            link = SmcNodeManagerClearPolicies._odata_id(
                vendor_payload.get("SmcNodeManager")
            )
            if link:
                return link
        return None

    def _get(self, uri, do_async):
        """GET a resource body for discovery.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async event loop when True.
        :return: the parsed response body, or {} for empty responses.
        """
        return self.base_query(uri, do_async=do_async).data or {}

    def _discover_node_managers(self, do_async):
        """Discover Node Managers that expose ClearAllPolicies.

        :param do_async: issue the underlying queries on the async loop when True.
        :return: list of ``{"Id", "uri", "system"}`` records.
        :raises Exception: when Redfish discovery reads fail.
        """
        managers = []
        seen = set()
        system_uris = self.discover_computer_system_ids() or []
        for system_uri in system_uris:
            node_manager_uri = self._node_manager_link(self._get(system_uri, do_async))
            if not node_manager_uri or node_manager_uri in seen:
                continue
            node_manager = self._get(node_manager_uri, do_async)
            actions = (
                node_manager.get("Actions")
                if isinstance(node_manager, dict)
                else None
            )
            if not isinstance(actions, dict) or _CLEAR_POLICIES_ACTION not in actions:
                continue
            seen.add(node_manager_uri)
            managers.append({
                "Id": node_manager.get("Id") or node_manager_uri.rsplit("/", 1)[-1],
                "uri": node_manager_uri,
                "system": system_uri,
            })
        return managers

    @staticmethod
    def _resolve_target(node_manager, managers):
        """Resolve a Node Manager Id or full URI to a discovered resource URI.

        :param node_manager: requested Node Manager Id or full Redfish URI.
        :param managers: records returned by :meth:`_discover_node_managers`.
        :return: the resolved Node Manager URI.
        :raises InvalidArgument: when empty, unknown, or ambiguous.
        """
        requested = (node_manager or "").strip()
        if not requested:
            raise InvalidArgument("node manager id or URI cannot be empty")
        if requested.startswith("/redfish"):
            match = next((m for m in managers if m["uri"] == requested), None)
            if match is not None:
                return match["uri"]
            raise InvalidArgument(
                f"no ClearAllPolicies-capable Node Manager at URI '{requested}'; "
                f"available: {[m['uri'] for m in managers]}"
            )
        wanted = requested.lower()
        matches = [m for m in managers if m["Id"].lower() == wanted]
        if not matches:
            raise InvalidArgument(
                f"no ClearAllPolicies-capable Node Manager with Id '{requested}'; "
                f"available: {[m['Id'] for m in managers]}"
            )
        if len(matches) > 1:
            raise InvalidArgument(
                f"Node Manager Id '{requested}' is ambiguous across "
                f"{[m['uri'] for m in matches]}; pass the full --node-manager URI"
            )
        return matches[0]["uri"]

    def execute(self,
                node_manager: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or clear Supermicro Node Manager policies.

        :param node_manager: Node Manager Id or full URI to clear; None lists
            capable resources.
        :param confirm: authorize the destructive ClearAllPolicies POST.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async loop
            when True.
        :return: CommandResult carrying the capable list, preview, or POST result.
        """
        managers = self._discover_node_managers(do_async)
        if node_manager is None:
            return CommandResult(
                {"node_managers": managers}, None, None, None
            )
        if not managers:
            raise InvalidArgument(
                "no ClearAllPolicies-capable Supermicro Node Manager resources "
                "found on this Redfish endpoint"
            )

        target_uri = self._resolve_target(node_manager, managers)
        return self.invoke_action(
            target_uri,
            "ClearAllPolicies",
            payload={},
            full_action_type=_CLEAR_POLICIES_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
