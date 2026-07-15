"""Read Redfish TelemetryService Triggers (metric alert thresholds).

    redfish_ctl telemetry-triggers

Walks ``/redfish/v1/TelemetryService/Triggers`` -> each Trigger, returning
{Id, MetricType, MetricProperties, Thresholds, TriggerActions}. Triggers are the
alert thresholds behind the telemetry a box publishes; iLO populates these
(they are typically empty/absent on Dell). Navigation is by link/``@odata.id``.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class TelemetryTriggers(RedfishManagerBase,
                        scm_type=ApiRequestType.Triggers,
                        name='telemetry-triggers',
                        metaclass=Singleton):
    """Read every TelemetryService Trigger (metric alert threshold)."""

    def __init__(self, *args, **kwargs):
        """Initialize the telemetry-triggers command."""
        super(TelemetryTriggers, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``telemetry-triggers`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read TelemetryService triggers (metric alert thresholds)"
        return cmd_parser, "telemetry-triggers", help_text

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
        """Walk the Triggers collection and summarize each trigger.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: when True, issue an expanded ($expand) query for the collection.
        :return: CommandResult wrapping the list of trigger summary rows.
        """
        rows = []
        triggers_uri = f"{RedfishApi.Version}/TelemetryService/Triggers"
        try:
            coll = self.base_query(triggers_uri, do_async=do_async,
                                   do_expanded=do_expanded).data or {}
        except Exception:
            return CommandResult(rows, None, None, None)

        for trig_uri in self._members(coll):
            try:
                t = self.base_query(trig_uri, do_async=do_async).data or {}
            except Exception:
                continue
            props = t.get("MetricProperties")
            rows.append({
                "Id": t.get("Id") or trig_uri.rsplit("/", 1)[-1],
                "MetricType": t.get("MetricType"),
                "MetricProperties": len(props) if isinstance(props, list) else 0,
                "NumericThresholds": t.get("NumericThresholds"),
                "TriggerActions": t.get("TriggerActions"),
            })
        return CommandResult(rows, None, None, None)
