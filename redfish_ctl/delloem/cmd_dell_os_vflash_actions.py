"""Preview or invoke Dell OS deployment VFlash actions.

    redfish_ctl dell-os-vflash-actions
    redfish_ctl dell-os-vflash-actions --action detach-vflash-iso
    redfish_ctl dell-os-vflash-actions --action skip-iso-boot --confirm

The command discovers the Dell OEM ``DellOSDeploymentService`` from ComputerSystem
links or the standard per-system OEM path. Selected actions preview by default,
and ``--confirm`` is required before any POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi


@dataclass(frozen=True)
class _OsDeploymentAction:
    """Selector metadata for one Dell OS deployment action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "boot-hd": _OsDeploymentAction(
        selector="boot-hd",
        full_type="#DellOSDeploymentService.BootToHD",
        action_name="BootToHD",
        description="boot back to the local hard disk path",
    ),
    "boot-vflash-iso": _OsDeploymentAction(
        selector="boot-vflash-iso",
        full_type="#DellOSDeploymentService.BootToISOFromVFlash",
        action_name="BootToISOFromVFlash",
        description="boot from the ISO currently staged on VFlash",
    ),
    "delete-vflash-iso": _OsDeploymentAction(
        selector="delete-vflash-iso",
        full_type="#DellOSDeploymentService.DeleteISOFromVFlash",
        action_name="DeleteISOFromVFlash",
        description="delete the ISO image currently staged on VFlash",
    ),
    "detach-drivers": _OsDeploymentAction(
        selector="detach-drivers",
        full_type="#DellOSDeploymentService.DetachDrivers",
        action_name="DetachDrivers",
        description="detach drivers exposed through OS deployment",
    ),
    "detach-vflash-iso": _OsDeploymentAction(
        selector="detach-vflash-iso",
        full_type="#DellOSDeploymentService.DetachISOFromVFlash",
        action_name="DetachISOFromVFlash",
        description="detach the VFlash ISO image from the host",
    ),
    "skip-iso-boot": _OsDeploymentAction(
        selector="skip-iso-boot",
        full_type="#DellOSDeploymentService.SkipISOImageBoot",
        action_name="SkipISOImageBoot",
        description="skip the pending ISO-image boot",
    ),
}


class DellOsVflashActions(RedfishManagerBase,
                          scm_type=ApiRequestType.DellOsVflashActions,
                          name="dell-os-vflash-actions",
                          metaclass=Singleton):
    """Discover and invoke Dell OS deployment VFlash actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-os-vflash-actions command."""
        super(DellOsVflashActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-os-vflash-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="OS deployment action to preview or invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--system",
            default=None,
            help="ComputerSystem Id or URI when multiple systems expose the service",
        )
        cmd_parser.add_argument(
            "--service-uri",
            dest="service_uri",
            default=None,
            help="specific DellOSDeploymentService URI to target",
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
            help="resolve the target and payload without POSTing",
        )
        return (
            cmd_parser,
            "dell-os-vflash-actions",
            "command run Dell OS deployment VFlash actions",
        )

    @staticmethod
    def _members(data):
        """Return collection member URIs from a Redfish collection.

        :param data: Redfish collection body.
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict)
            and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` from a linked Redfish property.

        :param data: resource body that may carry the link.
        :param key: property name to inspect.
        :return: linked URI, or None when absent.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _nested_link(data, *keys):
        """Follow nested dict keys and return the final ``@odata.id``.

        :param data: resource body to walk.
        :param keys: nested dict keys to follow.
        :return: linked URI, or None when any step is absent.
        """
        value = data
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return DellOsVflashActions._link({"value": value}, "value")

    @staticmethod
    def _resource_id(uri):
        """Return the trailing Redfish URI segment.

        :param uri: Redfish resource URI.
        :return: trailing URI segment.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _service_candidates(system_uri, system):
        """Return candidate DellOSDeploymentService URIs for one system.

        :param system_uri: ComputerSystem URI.
        :param system: ComputerSystem resource body.
        :return: ordered list of candidate service URIs.
        """
        linked = DellOsVflashActions._nested_link(
            system,
            "Links",
            "Oem",
            "Dell",
            "DellOSDeploymentService",
        )
        system_id = DellOsVflashActions._resource_id(system_uri)
        candidates = [
            linked,
            f"{system_uri.rstrip('/')}/Oem/Dell/DellOSDeploymentService",
            f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellOSDeploymentService",
        ]
        seen = set()
        result = []
        for uri in candidates:
            if uri and uri not in seen:
                seen.add(uri)
                result.append(uri)
        return result

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async path when True.
        :param optional: treat a failed read as an empty object when True.
        :return: parsed resource body.
        :raises InvalidArgument: when a required read fails or is not an object.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            if optional:
                return {}
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            if optional:
                return {}
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    def _discover_rows(self, do_async):
        """Discover DellOSDeploymentService resources and supported actions.

        :param do_async: issue GET requests on the async path when True.
        :return: list of discovered service rows.
        """
        systems = self._get(f"{RedfishApi.Version}/Systems", do_async)
        rows = []
        seen_services = set()
        for system_uri in self._members(systems):
            system = self._get(system_uri, do_async, optional=True)
            for service_uri in self._service_candidates(system_uri, system):
                if service_uri in seen_services:
                    continue
                seen_services.add(service_uri)
                service = self._get(service_uri, do_async, optional=True)
                targets = self._flatten_action_targets(service)
                actions = []
                for spec in _ACTION_SPECS.values():
                    target = targets.get(spec.full_type)
                    if target:
                        actions.append({
                            "Action": spec.selector,
                            "FullType": spec.full_type,
                            "Target": target,
                            "Description": spec.description,
                        })
                if actions:
                    rows.append({
                        "System": system.get("Id") or self._resource_id(system_uri),
                        "SystemUri": system_uri,
                        "Id": service.get("Id") or self._resource_id(service_uri),
                        "Name": service.get("Name"),
                        "Uri": service_uri,
                        "Actions": actions,
                    })
        return rows

    @staticmethod
    def _resolve_row(rows, system=None, service_uri=None):
        """Resolve a selected OS deployment service row.

        :param rows: discovered rows from :meth:`_discover_rows`.
        :param system: optional ComputerSystem Id or URI selector.
        :param service_uri: optional DellOSDeploymentService URI selector.
        :return: matching row.
        :raises InvalidArgument: when selection is missing or ambiguous.
        """
        matches = list(rows)
        if service_uri:
            wanted = service_uri.rstrip("/")
            matches = [row for row in rows if row["Uri"].rstrip("/") == wanted]
        elif system:
            wanted = system.rstrip("/")
            folded = wanted.lower()
            matches = [
                row for row in rows
                if row["SystemUri"].rstrip("/") == wanted
                or str(row["System"]).lower() == folded
            ]
        if not matches:
            raise InvalidArgument("DellOSDeploymentService resource not found")
        if len(matches) > 1:
            systems = [row["System"] for row in matches]
            raise InvalidArgument(
                "multiple DellOSDeploymentService resources found; pass --system "
                f"or --service-uri: {systems}"
            )
        return matches[0]

    def execute(self,
                action: Optional[str] = None,
                system: Optional[str] = None,
                service_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell OS deployment VFlash actions.

        :param action: optional action selector; None lists discovered targets.
        :param system: optional ComputerSystem Id or URI selector.
        :param service_uri: optional DellOSDeploymentService URI selector.
        :param confirm: authorize a POST. Without this every selected action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        :raises InvalidArgument: when selection arguments are invalid.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult({"os_deployment_targets": rows}, None, None, None)

        spec = _ACTION_SPECS[action]
        row = self._resolve_row(rows, system=system, service_uri=service_uri)
        return self.invoke_action(
            row["Uri"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
