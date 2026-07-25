"""Submit a Redfish telemetry test metric report.

    redfish_ctl telemetry-submit-test
    redfish_ctl telemetry-submit-test --confirm

Discovers TelemetryService.SubmitTestMetricReport from the service's Actions
block and builds the current TelemetryService payload shape. The command is
preview-only unless ``--confirm`` is supplied.
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_SUBMIT_TEST_ACTION = "#TelemetryService.SubmitTestMetricReport"


class TelemetrySubmitTest(IDracManager,
                          scm_type=ApiRequestType.TelemetrySubmitTest,
                          name="telemetry-submit-test",
                          metaclass=Singleton):
    """Submit a TelemetryService test metric report."""

    def __init__(self, *args, **kwargs):
        """Initialize the telemetry-submit-test command."""
        super(TelemetrySubmitTest, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``telemetry-submit-test`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--metric-report-name", required=False, dest="metric_report_name",
            type=str, default="redfish_ctl_test_metric_report",
            help="MetricReportName parameter for the generated test report")
        cmd_parser.add_argument(
            "--metric-id", required=False, dest="metric_id", type=str,
            default="redfish_ctl_test_metric",
            help="MetricId field for the generated test metric value")
        cmd_parser.add_argument(
            "--metric-value", required=False, dest="metric_value", type=str,
            default="1",
            help="MetricValue field for the generated test metric value")
        cmd_parser.add_argument(
            "--metric-property", required=False, dest="metric_property", type=str,
            default=None,
            help="optional MetricProperty URI for the generated test metric value")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="fire the SubmitTestMetricReport POST; without it the command previews")
        cmd_parser.add_argument(
            "--dry_run", action="store_true", dest="dry_run", default=False,
            help="resolve the target and show it without POSTing; overrides --confirm")
        return (
            cmd_parser,
            "telemetry-submit-test",
            "command submit a Redfish telemetry test metric report",
        )

    def _telemetry_service_uri(self, do_async):
        """Resolve the TelemetryService URI from the service root.

        :param do_async: issue the service-root query on the async path when True.
        :return: the TelemetryService ``@odata.id``, or the standard fallback URI.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        link = root.get("TelemetryService")
        if isinstance(link, dict) and link.get("@odata.id"):
            return link["@odata.id"]
        return f"{RedfishApi.Version}/TelemetryService"

    @staticmethod
    def _payload(metric_report_name, metric_id, metric_value, metric_property):
        """Build the SubmitTestMetricReport payload.

        GeneratedMetricReportValues and MetricReportName are the parameters
        defined by the TelemetryService.SubmitTestMetricReport action. Each
        generated metric item uses MetricId, MetricValue, and optional
        MetricProperty fields from the MetricValue schema.

        :param metric_report_name: generated report name to send.
        :param metric_id: generated metric identifier to send.
        :param metric_value: generated metric value to send as a string.
        :param metric_property: optional Redfish property URI for the metric.
        :return: the JSON-serializable action payload dict.
        """
        metric = {
            "MetricId": str(metric_id),
            "MetricValue": str(metric_value),
        }
        if metric_property:
            metric["MetricProperty"] = metric_property
        return {
            "MetricReportName": metric_report_name,
            "GeneratedMetricReportValues": [metric],
        }

    def execute(self,
                metric_report_name: Optional[str] = "redfish_ctl_test_metric_report",
                metric_id: Optional[str] = "redfish_ctl_test_metric",
                metric_value: Optional[str] = "1",
                metric_property: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or fire TelemetryService.SubmitTestMetricReport.

        The action is reversible, but synthetic metric reports may reach event
        destinations, so this command previews by default. ``--dry_run`` remains
        a no-POST override even when ``--confirm`` is also set.

        :param metric_report_name: ``MetricReportName`` value to send.
        :param metric_id: ``MetricId`` value for the generated metric.
        :param metric_value: ``MetricValue`` value for the generated metric.
        :param metric_property: optional ``MetricProperty`` URI for the metric.
        :param confirm: authorize the SubmitTestMetricReport POST to fire.
        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: a CommandResult with the POST outcome or dry-run preview.
        """
        preview_only = bool(dry_run) or not bool(confirm)
        result = self.invoke_action(
            self._telemetry_service_uri(do_async),
            "SubmitTestMetricReport",
            payload=self._payload(
                metric_report_name,
                metric_id,
                metric_value,
                metric_property,
            ),
            full_action_type=_SUBMIT_TEST_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=preview_only,
            confirm=True,
        )
        if (
                not confirm
                and not dry_run
                and result.error is None
                and isinstance(result.data, dict)):
            result.data["blocked"] = "test metric report submission requires --confirm"
        return result
