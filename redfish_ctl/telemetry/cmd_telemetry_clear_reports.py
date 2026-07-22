"""Clear generated Redfish TelemetryService MetricReports.

    redfish_ctl telemetry-clear-reports
    redfish_ctl telemetry-clear-reports --confirm

The command resolves ``#TelemetryService.ClearMetricReports`` from the
TelemetryService resource and previews by default. Clearing generated reports
removes BMC telemetry samples, so the action only POSTs when ``--confirm`` is
supplied.
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_CLEAR_METRIC_REPORTS_ACTION = "#TelemetryService.ClearMetricReports"


class TelemetryClearReports(IDracManager,
                            scm_type=ApiRequestType.TelemetryClearReports,
                            name="telemetry-clear-reports",
                            metaclass=Singleton):
    """Clear generated TelemetryService MetricReports."""

    def __init__(self, *args, **kwargs):
        """Initialize the telemetry-clear-reports command."""
        super(TelemetryClearReports, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``telemetry-clear-reports`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the ClearMetricReports POST; without it the command previews",
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
            "telemetry-clear-reports",
            "command clear generated TelemetryService MetricReports",
        )

    def execute(self,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or run TelemetryService.ClearMetricReports.

        :param confirm: authorize the ClearMetricReports POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query/POST on the async path.
        :return: CommandResult with the resolved action target, preview, or POST result.
        """
        return self.invoke_action(
            f"{RedfishApi.Version}/TelemetryService",
            "ClearMetricReports",
            payload={},
            full_action_type=_CLEAR_METRIC_REPORTS_ACTION,
            do_async=do_async,
            expected_status=204,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
