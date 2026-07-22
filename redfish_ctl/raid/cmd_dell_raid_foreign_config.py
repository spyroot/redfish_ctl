"""Preview or run DellRaidService foreign-configuration actions.

    redfish_ctl dell-raid-foreign-config
    redfish_ctl dell-raid-foreign-config --action import
    redfish_ctl dell-raid-foreign-config --action unlock-secure --confirm \
        --i-understand-irreversible

The command resolves DellRaidService from the selected ComputerSystem and lists
advertised foreign-configuration actions without mutating by default. Importing
or unlocking secure foreign RAID configuration can alter storage state, so both
``--confirm`` and ``--i-understand-irreversible`` are required before POST.
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_IMPORT_FOREIGN_CONFIG_ACTION = "#DellRaidService.ImportForeignConfig"
_UNLOCK_SECURE_FOREIGN_CONFIG_ACTION = (
    "#DellRaidService.UnLockSecureForeignConfig"
)
_SYSTEM_FALLBACK = f"{RedfishApi.Version}/Systems/System.Embedded.1"
_SERVICE_SUFFIX = "Oem/Dell/DellRaidService"


@dataclass(frozen=True)
class _ForeignConfigAction:
    """Static selector metadata for one DellRaidService foreign action."""

    selector: str
    full_type: str
    action_name: str


_ACTION_SPECS = {
    "import": _ForeignConfigAction(
        selector="import",
        full_type=_IMPORT_FOREIGN_CONFIG_ACTION,
        action_name="ImportForeignConfig",
    ),
    "unlock-secure": _ForeignConfigAction(
        selector="unlock-secure",
        full_type=_UNLOCK_SECURE_FOREIGN_CONFIG_ACTION,
        action_name="UnLockSecureForeignConfig",
    ),
}


class DellRaidForeignConfigActions(
    IDracManager,
    scm_type=ApiRequestType.DellRaidForeignConfigActions,
    name="dell-raid-foreign-config",
    metaclass=Singleton,
):
    """Preview or run DellRaidService foreign-configuration actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-raid-foreign-config command."""
        super(DellRaidForeignConfigActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-raid-foreign-config`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="foreign-configuration action to preview or invoke",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="authorize the selected DellRaidService POST",
        )
        cmd_parser.add_argument(
            "--i-understand-irreversible",
            action="store_true",
            dest="confirm_irreversible",
            default=False,
            help="required with --confirm because the action changes RAID state",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides confirmation",
        )
        return (
            cmd_parser,
            "dell-raid-foreign-config",
            "preview or run Dell RAID foreign-configuration actions",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _oem_dell_link(cls, resource, key):
        """Return a Dell OEM link from ``Links.Oem.Dell`` or ``Oem.Dell``.

        :param resource: Redfish resource body to inspect.
        :param key: Dell OEM link name.
        :return: the linked URI, or None when absent.
        """
        links = (resource or {}).get("Links") or {}
        linked = cls._link(((links.get("Oem") or {}).get("Dell") or {}), key)
        if linked:
            return linked
        return cls._link((((resource or {}).get("Oem") or {}).get("Dell") or {}), key)

    @staticmethod
    def _clean_required(value, label):
        """Normalize a required string option.

        :param value: option value to normalize.
        :param label: user-facing field label for errors.
        :return: stripped string.
        :raises InvalidArgument: when the value is missing or empty.
        """
        stripped = (value or "").strip()
        if not stripped:
            raise InvalidArgument(f"{label} cannot be empty")
        return stripped

    def _system_uri(self):
        """Return the selected ComputerSystem URI.

        :return: manager-selected system URI, or Dell default fallback.
        """
        try:
            system_uri = self.idrac_manage_servers
        except Exception:
            system_uri = ""
        return system_uri or _SYSTEM_FALLBACK

    def _read_optional(self, uri, do_async):
        """Read a Redfish resource, returning ``None`` on lookup failure.

        :param uri: Redfish resource URI.
        :param do_async: issue the query over the async Redfish path.
        :return: resource body when readable, otherwise None.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _raid_service(self, do_async):
        """Resolve and read the DellRaidService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on failure.
        """
        system_uri = self._system_uri()
        system = self._read_optional(system_uri, do_async) or {}
        system_id = system_uri.rstrip("/").rsplit("/", 1)[-1]
        candidates = [
            self._oem_dell_link(system, "DellRaidService"),
            f"{system_uri}/{_SERVICE_SUFFIX}",
            f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellRaidService",
        ]
        seen = set()
        for uri in candidates:
            if not uri or uri in seen:
                continue
            seen.add(uri)
            data = self._read_optional(uri, do_async)
            if data is not None:
                return uri, data
        fallback = next(
            (uri for uri in candidates if uri),
            f"{system_uri}/{_SERVICE_SUFFIX}",
        )
        return (
            fallback,
            CommandResult(
                None,
                None,
                None,
                "DellRaidService is not available on this host",
            ),
        )

    def _discover_rows(self, do_async):
        """Discover available foreign-configuration action targets.

        :param do_async: issue underlying queries over the async path.
        :return: tuple of service URI, discovered action map, and rows.
        """
        raid_uri, service = self._raid_service(do_async)
        if isinstance(service, CommandResult):
            return raid_uri, {}, service
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _ACTION_SPECS.values():
            target = targets.get(spec.full_type)
            if not target:
                continue
            rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Resource": raid_uri,
                "Target": target,
                "RequiredPayload": [],
                "Level": "irreversible",
            })
        return raid_uri, actions, rows

    def execute(self,
                action: Optional[str] = None,
                confirm: Optional[bool] = False,
                confirm_irreversible: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run DellRaidService foreign actions.

        :param action: selected action: ``import`` or ``unlock-secure``.
        :param confirm: authorize the DellRaidService POST.
        :param confirm_irreversible: extra irreversible confirmation token.
        :param dry_run: resolve the target without POSTing; overrides confirmation.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        raid_uri, actions, rows = self._discover_rows(bool(do_async))
        if isinstance(rows, CommandResult):
            return rows
        if action is None:
            return CommandResult(
                {"raid_service": raid_uri, "actions": rows},
                actions,
                None,
                None,
            )

        selected = self._clean_required(action, "action")
        if selected not in _ACTION_SPECS:
            raise InvalidArgument("action must be one of: import, unlock-secure")
        spec = _ACTION_SPECS[selected]
        if not any(row["Action"] == selected for row in rows):
            return CommandResult(
                {
                    "raid_service": raid_uri,
                    "action": spec.full_type,
                    "available": rows,
                },
                actions,
                None,
                f"action '{spec.full_type}' not found on {raid_uri}",
            )
        return self.invoke_action(
            raid_uri,
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
            confirm_irreversible=bool(confirm_irreversible),
        )
