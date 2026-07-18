"""Show Dell system errors on the chassis LCD.

    redfish_ctl dell-system-lcd-errors
    redfish_ctl dell-system-lcd-errors --system-uri /redfish/v1/Systems/System.Embedded.1
    redfish_ctl dell-system-lcd-errors --confirm

The command resolves ``#DellSystemManagementService.ShowErrorsOnLCD`` from the
Dell SystemManagementService resource beneath a ComputerSystem. The action is
treated as destructive by the shared policy, so the default invocation previews
the target and payload without POSTing.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_SHOW_ERRORS_ON_LCD_ACTION = "#DellSystemManagementService.ShowErrorsOnLCD"
_DELL_SYSTEM_MANAGEMENT_SERVICE = "Oem/Dell/DellSystemManagementService"


class DellSystemLcdErrors(RedfishManagerBase,
                          scm_type=ApiRequestType.DellSystemLcdErrors,
                          name="dell-system-lcd-errors",
                          metaclass=Singleton):
    """Preview or invoke DellSystemManagementService.ShowErrorsOnLCD."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-system-lcd-errors command."""
        super(DellSystemLcdErrors, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-system-lcd-errors`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--system-uri",
            required=False,
            dest="system_uri",
            type=str,
            default=None,
            help="ComputerSystem URI that owns DellSystemManagementService; "
                 "omitted value probes discovered systems",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the ShowErrorsOnLCD POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-system-lcd-errors",
            "command show Dell system errors on the chassis LCD",
        )

    @staticmethod
    def _normalize_uri(uri):
        """Return a Redfish URI without a trailing slash.

        :param uri: Redfish resource URI.
        :return: normalized URI, or None for a blank value.
        """
        if uri is None:
            return None
        normalized = uri.strip().rstrip("/")
        return normalized or None

    @classmethod
    def _service_uri(cls, system_uri):
        """Return the DellSystemManagementService URI below a ComputerSystem.

        :param system_uri: normalized ComputerSystem URI.
        :return: SystemManagementService URI.
        """
        return f"{system_uri}/{_DELL_SYSTEM_MANAGEMENT_SERVICE}"

    def _candidate_systems(self, system_uri=None):
        """Return ComputerSystem URIs to probe for the Dell service.

        :param system_uri: optional explicit ComputerSystem URI.
        :return: de-duplicated list of normalized system URIs.
        """
        explicit = self._normalize_uri(system_uri)
        if explicit is not None:
            return [explicit]

        candidates = []
        try:
            candidates.extend(self.discover_computer_system_ids())
        except Exception:
            pass
        try:
            candidates.append(self.idrac_manage_servers)
        except Exception:
            pass

        seen = set()
        normalized = []
        for candidate in candidates:
            value = self._normalize_uri(candidate)
            if value is not None and value not in seen:
                normalized.append(value)
                seen.add(value)
        return normalized

    def _show_errors_metadata(self, do_async, system_uri=None):
        """Resolve the Dell ShowErrorsOnLCD action target.

        :param do_async: issue Redfish queries on the async path.
        :param system_uri: optional explicit ComputerSystem URI.
        :return: CommandResult with target metadata, or an error.
        """
        candidates = self._candidate_systems(system_uri)
        if not candidates:
            return CommandResult(
                {
                    "action": _SHOW_ERRORS_ON_LCD_ACTION,
                    "available": [],
                },
                None,
                None,
                "no ComputerSystem URI available for DellSystemManagementService",
            )

        attempts = []
        last_actions = None
        for candidate in candidates:
            service_uri = self._service_uri(candidate)
            attempts.append(service_uri)
            try:
                service = self.base_query(service_uri, do_async=do_async).data or {}
            except Exception:
                continue
            actions = self.discover_redfish_actions(self, service)
            targets = self._flatten_action_targets(service)
            target = targets.get(_SHOW_ERRORS_ON_LCD_ACTION)
            if target is None:
                last_actions = actions
                continue
            return CommandResult(
                {
                    "system": candidate,
                    "system_management_service": service_uri,
                    "action": _SHOW_ERRORS_ON_LCD_ACTION,
                    "target": target,
                },
                actions,
                None,
                None,
            )

        available = []
        if last_actions is not None:
            available = sorted(
                set(list(last_actions.keys()) + list(targets.keys()))
            )
        return CommandResult(
            {
                "action": _SHOW_ERRORS_ON_LCD_ACTION,
                "attempted": attempts,
                "available": available,
            },
            last_actions,
            None,
            (
                f"action '{_SHOW_ERRORS_ON_LCD_ACTION}' not found on "
                "DellSystemManagementService"
            ),
        )

    def execute(self,
                system_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or run DellSystemManagementService.ShowErrorsOnLCD.

        :param system_uri: optional ComputerSystem URI owning the Dell service.
        :param confirm: authorize the ShowErrorsOnLCD POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: CommandResult with target metadata, preview, or POST result.
        """
        metadata = self._show_errors_metadata(do_async, system_uri=system_uri)
        if metadata.error is not None:
            return metadata

        result = self.invoke_action(
            metadata.data["system_management_service"],
            "ShowErrorsOnLCD",
            payload={},
            full_action_type=_SHOW_ERRORS_ON_LCD_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if isinstance(result.data, dict):
            result.data.setdefault("system", metadata.data["system"])
            result.data.setdefault(
                "system_management_service",
                metadata.data["system_management_service"],
            )
        return result
