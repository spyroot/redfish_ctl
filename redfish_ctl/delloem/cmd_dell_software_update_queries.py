"""Run Dell SoftwareInstallationService read-only query actions.

    redfish_ctl dell-software-update-queries
    redfish_ctl dell-software-update-queries --query schedule
    redfish_ctl dell-software-update-queries --query repo-list --dry_run

Dell carries these query operations over Redfish Actions, so the command
discovers the target from ``DellSoftwareInstallationService`` and POSTs only the
read-only query actions named here. Omitting ``--query`` lists available targets
without POSTing.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_FALLBACK = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)


@dataclass(frozen=True)
class _DellSoftwareQuerySpec:
    """Selector metadata for one Dell software-installation query action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_QUERY_SPECS = {
    "repo-list": _DellSoftwareQuerySpec(
        selector="repo-list",
        full_type="#DellSoftwareInstallationService.GetRepoBasedUpdateList",
        action_name="GetRepoBasedUpdateList",
        description="get repository-based update list state",
    ),
    "schedule": _DellSoftwareQuerySpec(
        selector="schedule",
        full_type="#DellSoftwareInstallationService.GetUpdateSchedule",
        action_name="GetUpdateSchedule",
        description="get the configured update schedule",
    ),
}


class DellSoftwareUpdateQueries(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellSoftwareUpdateQueries,
    name="dell-software-update-queries",
    metaclass=Singleton,
):
    """Discover and run Dell software-installation read-only query actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-software-update-queries command."""
        super(DellSoftwareUpdateQueries, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-software-update-queries`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--query",
            choices=sorted(_QUERY_SPECS),
            default=None,
            help="query action to run; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing",
        )
        return (
            cmd_parser,
            "dell-software-update-queries",
            "command run Dell software installation query actions",
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
    def _dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, treating optional misses as absent.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _system_uris(self, do_async):
        """Return ComputerSystem member URIs.

        :param do_async: run the query asynchronously when True.
        :return: list of system resource URIs.
        """
        root = self._get(RedfishApi.Version, do_async)
        systems_uri = self._link(root, "Systems") or f"{RedfishApi.Version}/Systems"
        systems = self._get(systems_uri, do_async)
        members = systems.get("Members") if isinstance(systems, dict) else []
        return [
            member["@odata.id"]
            for member in members
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    def _service_uris(self, do_async):
        """Discover DellSoftwareInstallationService resource URIs.

        :param do_async: run the query asynchronously when True.
        :return: ordered list of discovered service URIs.
        """
        uris = []
        for system_uri in self._system_uris(do_async):
            system = self._get(system_uri, do_async)
            service_uri = self._link(
                self._dell(system),
                "DellSoftwareInstallationService",
            )
            if service_uri and service_uri not in uris:
                uris.append(service_uri)
        if not uris:
            uris.append(_SERVICE_FALLBACK)
        return uris

    def _discover_rows(self, do_async):
        """Discover available Dell software update query actions.

        :param do_async: run underlying GETs asynchronously when True.
        :return: list of available query-action rows.
        """
        rows = []
        for service_uri in self._service_uris(do_async):
            service = self._get(service_uri, do_async)
            targets = self._flatten_action_targets(service)
            for spec in _QUERY_SPECS.values():
                target = targets.get(spec.full_type)
                if target:
                    rows.append({
                        "Query": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": service_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    def execute(self,
                query: Optional[str] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or run Dell software-installation query actions.

        :param query: selector from ``_QUERY_SPECS``; omit to list targets.
        :param dry_run: resolve the target and payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if query is None:
            return CommandResult(rows, None, None, None)

        matches = [row for row in rows if row["Query"] == query]
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell software update query not found: {query}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                f"multiple Dell software update query targets found: {query}",
            )

        spec = _QUERY_SPECS[query]
        return self.invoke_action(
            matches[0]["Resource"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=False,
        )
