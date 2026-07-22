"""Reset Redfish TelemetryService metric report definitions to defaults.

    redfish_ctl telemetry-reset-definitions
    redfish_ctl telemetry-reset-definitions --confirm
    redfish_ctl telemetry-reset-definitions --dry_run

The command resolves the service root's ``TelemetryService`` link and invokes
the service resource's ``#TelemetryService.ResetMetricReportDefinitionsToDefaults``
action. Resetting definitions rewrites telemetry configuration, so the shared
action guard treats it as destructive and previews by default unless
``--confirm`` is present.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_RESET_DEFINITIONS_ACTION = "#TelemetryService.ResetMetricReportDefinitionsToDefaults"


class TelemetryResetMetricDefinitions(
        IDracManager,
        scm_type=ApiRequestType.TelemetryResetMetricDefinitions,
        name="telemetry-reset-definitions",
        metaclass=Singleton):
    """Reset TelemetryService metric report definitions to vendor defaults."""

    def __init__(self, *args, **kwargs):
        """Initialize the telemetry-reset-definitions command."""
        super(TelemetryResetMetricDefinitions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``telemetry-reset-definitions`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the reset POST; without it the command only previews",
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
            "telemetry-reset-definitions",
            "command reset TelemetryService metric report definitions to defaults",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: parsed Redfish resource that may hold the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: linked Redfish URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _telemetry_service_uri(self, do_async):
        """Resolve the TelemetryService URI from the service root.

        :param do_async: issue the service-root query over the async path.
        :return: the linked TelemetryService URI, or the standard fallback URI.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        return self._link(root, "TelemetryService") or f"{RedfishApi.Version}/TelemetryService"

    def execute(self,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and invoke the metric-report-definition reset action.

        :param confirm: authorize the destructive reset POST to run.
        :param dry_run: force a dry-run preview even when ``confirm`` is true.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with a dry-run preview, POST result, or a
            missing-action error from the TelemetryService resource.
        """
        return self.invoke_action(
            self._telemetry_service_uri(bool(do_async)),
            "ResetMetricReportDefinitionsToDefaults",
            payload={},
            full_action_type=_RESET_DEFINITIONS_ACTION,
            do_async=do_async,
            expected_status=204,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
