"""Cancel selected DellRaidService background operations.

    redfish_ctl dell-raid-cancel-actions
    redfish_ctl dell-raid-cancel-actions --action check-consistency
    redfish_ctl dell-raid-cancel-actions --action check-consistency --confirm

The command discovers cancel actions from the DellRaidService resource exposed
by the managed ComputerSystem. Canceling RAID work can interrupt storage
operations, so selected actions preview by default and only POST when
``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton


@dataclass(frozen=True)
class _DellRaidCancelSpec:
    """Static selector metadata for one DellRaidService cancel action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "background-init": _DellRaidCancelSpec(
        selector="background-init",
        full_type="#DellRaidService.CancelBackgroundInitialization",
        action_name="CancelBackgroundInitialization",
        description="cancel background virtual-disk initialization",
    ),
    "check-consistency": _DellRaidCancelSpec(
        selector="check-consistency",
        full_type="#DellRaidService.CancelCheckConsistency",
        action_name="CancelCheckConsistency",
        description="cancel virtual-disk consistency checking",
    ),
    "rebuild-physical-disk": _DellRaidCancelSpec(
        selector="rebuild-physical-disk",
        full_type="#DellRaidService.CancelRebuildPhysicalDisk",
        action_name="CancelRebuildPhysicalDisk",
        description="cancel physical-disk rebuild",
    ),
}


class DellRaidCancelActions(IDracManager,
                            scm_type=ApiRequestType.DellRaidCancelActions,
                            name="dell-raid-cancel-actions",
                            metaclass=Singleton):
    """Discover and invoke DellRaidService cancel actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-cancel-actions command."""
        super(DellRaidCancelActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-raid-cancel-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="DellRaidService cancel action to preview or run; omit to list",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellRaidService URI when more than one target exists",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected cancel action instead of previewing it",
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
            "dell-raid-cancel-actions",
            "command cancel Dell RAID background operations",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _links_oem_dell(data):
        """Return ``Links.Oem.Dell`` from a Redfish resource.

        :param data: Redfish resource body.
        :return: Dell OEM link block, or an empty dict.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _oem_dell(data):
        """Return ``Oem.Dell`` from a Redfish resource.

        :param data: Redfish resource body.
        :return: Dell OEM extension block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating missing optional resources.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _system_uris(self):
        """Return candidate ComputerSystem URIs, host first.

        :return: ordered, de-duplicated ComputerSystem URIs.
        """
        candidates = []
        try:
            host = self.idrac_manage_servers
        except Exception:
            host = ""
        if host:
            candidates.append(host)
        try:
            candidates.extend(self.discover_computer_system_ids() or [])
        except Exception:
            pass
        return list(dict.fromkeys(candidates))

    def _raid_service_uris(self, do_async):
        """Return candidate DellRaidService URIs for managed systems.

        :param do_async: run underlying GETs asynchronously when True.
        :return: ordered, de-duplicated DellRaidService URIs.
        """
        uris = []
        for system_uri in self._system_uris():
            system = self._get(system_uri, do_async)
            links_dell = self._links_oem_dell(system)
            service_uri = self._link(links_dell, "DellRaidService")
            if not service_uri:
                oem_dell = self._oem_dell(system)
                service_uri = self._link(oem_dell, "DellRaidService")
            if service_uri:
                uris.append(service_uri)

            base = system_uri.rstrip("/")
            system_id = base.rsplit("/", 1)[-1]
            uris.append(f"{base}/Oem/Dell/DellRaidService")
            uris.append(f"/redfish/v1/Dell/Systems/{system_id}/DellRaidService")
        return list(dict.fromkeys(uris))

    def _discover_rows(self, do_async):
        """Discover available DellRaidService cancel actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available cancel-action rows.
        """
        rows = []
        for service_uri in self._raid_service_uris(do_async):
            service = self._get(service_uri, do_async)
            targets = self._flatten_action_targets(service)
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": service_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, resource_uri):
        """Filter discovered rows by action selector and optional resource URI.

        :param rows: discovered Dell RAID cancel rows.
        :param action: selected action name.
        :param resource_uri: optional service URI selector.
        :return: matching rows.
        """
        matches = [row for row in rows if row["Action"] == action]
        if resource_uri:
            normalized = resource_uri.rstrip("/")
            matches = [
                row for row in matches
                if row["Resource"].rstrip("/") == normalized
            ]
        return matches

    def execute(self,
                action: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or invoke DellRaidService cancel actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional service URI to disambiguate multiple targets.
        :param confirm: POST the selected action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = self._matches(rows, action, resource_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell RAID cancel action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell RAID cancel targets found; pass --resource-uri",
            )

        spec = _ACTION_SPECS[action]
        row = matches[0]
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
