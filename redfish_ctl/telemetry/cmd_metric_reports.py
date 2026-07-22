"""Read Redfish TelemetryService MetricReports (out-of-band, incl. GPU/accelerator).

    redfish_ctl metric-reports
    redfish_ctl metric-reports --report ProcessorMetrics

Walks ``/redfish/v1/TelemetryService/MetricReports`` -> each MetricReport ->
its ``MetricValues``, flattening to {Report, MetricProperty, MetricValue,
Timestamp}. Navigation is by links and ``@odata.id`` with no hardcoded ids, so
it works on any host exposing TelemetryService (Dell, Supermicro/OpenBMC, HPE).

On an NVIDIA HGX/GB300 baseboard this surfaces GPU/NVLink MetricReports
out-of-band — telemetry an in-band agent (DCGM) cannot read when the node is
powered off or pre-OS. ``MetricValue`` is always a Redfish string and
``MetricId`` is frequently absent, so each sample is keyed by its
``MetricProperty`` and the raw string value is preserved verbatim.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class MetricReports(IDracManager,
                    scm_type=ApiRequestType.MetricReports,
                    name='metric-reports',
                    metaclass=Singleton):
    """Read every Redfish TelemetryService MetricReport (platform + accelerator OOB metrics)."""

    def __init__(self, *args, **kwargs):
        """Initialize the metric-reports command."""
        super(MetricReports, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``metric-reports`` subcommand and its one optional filter.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--report', required=False, dest="report", default=None, type=str,
            help="only reports whose id contains this substring, e.g. ProcessorMetrics")
        help_text = "command read TelemetryService metric reports (incl. OOB GPU)"
        return cmd_parser, "metric-reports", help_text

    @staticmethod
    def _members(data):
        """Return the @odata.id strings from a Redfish collection, tolerantly.

        :param data: parsed Redfish collection resource, or any value.
        :return: list of member @odata.id strings; empty when data is not a collection.
        """
        if not isinstance(data, dict):
            return []
        return [m["@odata.id"] for m in data.get("Members", [])
                if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

    def execute(self,
                report: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk MetricReports and flatten each MetricValue into a flat row.

        Tolerant of a host without TelemetryService, an unreachable report, or a
        non-dict sample (each is skipped). ``report`` narrows to reports whose id
        contains that substring (case-insensitive) — e.g. ``ProcessorMetrics`` to
        scope to GPU/CPU processor telemetry on an HGX baseboard.

        :param report: if set, keep only reports whose id contains this substring
            (case-insensitive); None returns every report.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: when True, issue an expanded ($expand) query for the collection.
        :return: CommandResult wrapping the flattened metric-value rows.
        """
        rows = []
        reports_uri = f"{RedfishApi.Version}/TelemetryService/MetricReports"
        try:
            coll = self.base_query(reports_uri, do_async=do_async,
                                   do_expanded=do_expanded).data or {}
        except Exception:
            return CommandResult(rows, None, None, None)

        for report_uri in self._members(coll):
            rid = report_uri.rsplit("/", 1)[-1]
            if report and report.lower() not in rid.lower():
                continue
            try:
                rdata = self.base_query(report_uri, do_async=do_async).data or {}
            except Exception:
                continue
            for sample in rdata.get("MetricValues", []):
                if not isinstance(sample, dict):
                    continue
                rows.append({
                    "Report": rid,
                    "MetricId": sample.get("MetricId"),
                    "MetricProperty": sample.get("MetricProperty"),
                    "MetricValue": sample.get("MetricValue"),
                    "Timestamp": sample.get("Timestamp"),
                })
        return CommandResult(rows, None, None, None)
