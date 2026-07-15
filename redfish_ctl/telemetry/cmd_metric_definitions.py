"""Read Redfish TelemetryService MetricReportDefinitions (what each report contains).

    redfish_ctl metric-definitions

Walks ``/redfish/v1/TelemetryService/MetricReportDefinitions`` -> each definition,
returning {Definition, Type, Report, MetricCount}. This is the companion to the
``metric-reports`` command: metric-reports gives the live values, this gives the
shape — which metrics each report carries, the collection type (OnRequest /
Periodic), and the report it feeds.

On this GB300 tree ``TelemetryService.MetricDefinitions`` is null; the populated
link is ``MetricReportDefinitions``. Navigation is by link/``@odata.id`` with no
hardcoded ids, so it works on any host exposing TelemetryService.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class MetricDefinitions(RedfishManagerBase,
                        scm_type=ApiRequestType.MetricReportDefinitions,
                        name='metric-definitions',
                        metaclass=Singleton):
    """Read every TelemetryService MetricReportDefinition (the report schemas)."""

    def __init__(self, *args, **kwargs):
        """Initialize the metric-definitions command."""
        super(MetricDefinitions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``metric-definitions`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read TelemetryService metric report definitions"
        return cmd_parser, "metric-definitions", help_text

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
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk MetricReportDefinitions and summarize each definition.

        Tolerant of a host without TelemetryService or the definitions link
        (returns an empty list). ``MetricProperties`` is the list of metric URIs
        the report publishes; its length is the report's metric count.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: when True, issue an expanded ($expand) query for the collection.
        :return: CommandResult wrapping the list of metric-definition summary rows.
        """
        rows = []
        defs_uri = f"{RedfishApi.Version}/TelemetryService/MetricReportDefinitions"
        try:
            coll = self.base_query(defs_uri, do_async=do_async,
                                   do_expanded=do_expanded).data or {}
        except Exception:
            return CommandResult(rows, None, None, None)

        for def_uri in self._members(coll):
            try:
                ddata = self.base_query(def_uri, do_async=do_async).data or {}
            except Exception:
                continue
            report = ddata.get("MetricReport") or {}
            report_id = report.get("@odata.id", "").rsplit("/", 1)[-1] if isinstance(report, dict) else None
            props = ddata.get("MetricProperties")
            rows.append({
                "Definition": ddata.get("Id") or def_uri.rsplit("/", 1)[-1],
                "Type": ddata.get("MetricReportDefinitionType"),
                "Report": report_id,
                "MetricCount": len(props) if isinstance(props, list) else 0,
            })
        return CommandResult(rows, None, None, None)
