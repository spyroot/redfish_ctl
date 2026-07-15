"""Read Redfish EnvironmentMetrics resources.

    redfish_ctl environment-metrics

Walks the chassis, processor, and memory collections and rolls up the
power, energy, temperature, and power-limit readings exposed by each
linked EnvironmentMetrics resource.
"""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class EnvironmentMetrics(RedfishManagerBase,
                         scm_type=ApiRequestType.EnvironmentMetrics,
                         name="environment-metrics",
                         metaclass=Singleton):
    """Read power, energy, and temperature rollups from EnvironmentMetrics."""

    def __init__(self, *args, **kwargs):
        """Initialize the environment-metrics command."""
        super(EnvironmentMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``environment-metrics`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read EnvironmentMetrics rollups"
        return cmd_parser, "environment-metrics", help_text

    @staticmethod
    def _members(data):
        """Extract member ``@odata.id`` links from a Redfish collection.

        :param data: parsed collection payload holding a ``Members`` array.
        :return: list of member URI strings, empty when data is not a collection.
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
        """Resolve a single ``@odata.id`` link stored under a key.

        :param data: parsed Redfish resource payload.
        :param key: property name holding a link object.
        :return: the linked URI string, or None when absent.
        """
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            return value["@odata.id"]
        return None

    @staticmethod
    def _resource_id(uri):
        """Return the trailing identifier segment of a Redfish URI.

        :param uri: resource path such as ``/redfish/v1/Chassis/1``.
        :return: the last path segment, for example ``1``.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _reading(data, key):
        """Read a metric's ``Reading`` value from a payload.

        :param data: parsed EnvironmentMetrics payload.
        :param key: metric property name such as ``PowerWatts``.
        :return: the ``Reading`` value when the metric is an object, otherwise the raw value.
        """
        metric = data.get(key) if isinstance(data, dict) else None
        if isinstance(metric, dict):
            return metric.get("Reading")
        return metric

    @staticmethod
    def _power_limit(data):
        """Collect the ``PowerLimitWatts`` control fields from a payload.

        :param data: parsed EnvironmentMetrics payload.
        :return: dict of the power-limit control fields, or None when absent.
        """
        metric = data.get("PowerLimitWatts") if isinstance(data, dict) else None
        if not isinstance(metric, dict):
            return None
        return {
            "Reading": metric.get("Reading"),
            "SetPoint": metric.get("SetPoint"),
            "DefaultSetPoint": metric.get("DefaultSetPoint"),
            "AllowableMin": metric.get("AllowableMin"),
            "AllowableMax": metric.get("AllowableMax"),
            "ControlMode": metric.get("ControlMode"),
        }

    def _query_optional(self, uri, do_async=False):
        """Query a URI and return its payload, swallowing any error.

        :param uri: Redfish resource path to fetch.
        :param do_async: when True, issue the query asynchronously.
        :return: the parsed payload dict, or an empty dict on failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _append_metric(self,
                       rows,
                       seen,
                       parent_type,
                       parent_uri,
                       metrics_uri,
                       do_async=False):
        """Fetch one EnvironmentMetrics resource and append its flattened row.

        :param rows: accumulator list receiving the flattened metric row.
        :param seen: set of already-processed metrics URIs for de-duplication.
        :param parent_type: resource type owning the metrics, such as ``Chassis``.
        :param parent_uri: URI of the parent resource.
        :param metrics_uri: URI of the EnvironmentMetrics resource to read.
        :param do_async: when True, issue the query asynchronously.
        """
        if not metrics_uri or metrics_uri in seen:
            return
        metrics = self._query_optional(metrics_uri, do_async=do_async)
        if not isinstance(metrics, dict) or not metrics:
            return
        seen.add(metrics_uri)
        rows.append({
            "ParentType": parent_type,
            "ParentId": self._resource_id(parent_uri),
            "ParentUri": parent_uri,
            "MetricsUri": metrics.get("@odata.id", metrics_uri),
            "Name": metrics.get("Name"),
            "PowerWatts": self._reading(metrics, "PowerWatts"),
            "EnergyJoules": self._reading(metrics, "EnergyJoules"),
            "EnergykWh": self._reading(metrics, "EnergykWh"),
            "TemperatureCelsius": self._reading(metrics, "TemperatureCelsius"),
            "PowerLimitWatts": self._power_limit(metrics),
            "FanSpeedsPercent": metrics.get("FanSpeedsPercent"),
        })

    def _append_chassis_metrics(self, rows, seen, do_async=False):
        """Append EnvironmentMetrics rows for every chassis.

        :param rows: accumulator list receiving the metric rows.
        :param seen: set of already-processed metrics URIs for de-duplication.
        :param do_async: when True, issue queries asynchronously.
        """
        chassis = self.base_query(REDFISH_API.Chassis, do_async=do_async)
        for chassis_uri in self._members(chassis.data):
            chassis_data = self._query_optional(chassis_uri, do_async=do_async)
            metrics_uri = self._link(chassis_data, "EnvironmentMetrics")
            self._append_metric(
                rows,
                seen,
                "Chassis",
                chassis_uri,
                metrics_uri,
                do_async=do_async,
            )

    def _append_system_collection_metrics(self,
                                          rows,
                                          seen,
                                          collection_key,
                                          parent_type,
                                          do_async=False):
        """Append EnvironmentMetrics rows for a per-system subresource collection.

        :param rows: accumulator list receiving the metric rows.
        :param seen: set of already-processed metrics URIs for de-duplication.
        :param collection_key: system link key to walk, such as ``Processors`` or ``Memory``.
        :param parent_type: resource type label recorded for each row.
        :param do_async: when True, issue queries asynchronously.
        """
        systems = self._query_optional(RedfishApi.Systems, do_async=do_async)
        for system_uri in self._members(systems):
            system = self._query_optional(system_uri, do_async=do_async)
            collection_uri = self._link(system, collection_key)
            if not collection_uri:
                continue
            collection = self._query_optional(collection_uri, do_async=do_async)
            for member_uri in self._members(collection):
                member = self._query_optional(member_uri, do_async=do_async)
                metrics_uri = self._link(member, "EnvironmentMetrics")
                self._append_metric(
                    rows,
                    seen,
                    parent_type,
                    member_uri,
                    metrics_uri,
                    do_async=do_async,
                )

    @staticmethod
    def _summary(rows):
        """Tally resource and reading counts across collected rows.

        :param rows: list of flattened metric rows.
        :return: dict of aggregate counts by parent type and reading kind.
        """
        by_parent = {}
        for row in rows:
            parent_type = row["ParentType"]
            by_parent[parent_type] = by_parent.get(parent_type, 0) + 1
        return {
            "resources": len(rows),
            "chassis_resources": by_parent.get("Chassis", 0),
            "processor_resources": by_parent.get("Processor", 0),
            "memory_resources": by_parent.get("Memory", 0),
            "power_watts": sum(
                1 for row in rows if row["PowerWatts"] is not None),
            "energy_joules": sum(
                1 for row in rows if row["EnergyJoules"] is not None),
            "energy_kwh": sum(
                1 for row in rows if row["EnergykWh"] is not None),
            "temperature_celsius": sum(
                1 for row in rows if row["TemperatureCelsius"] is not None),
            "power_limits": sum(
                1 for row in rows if row["PowerLimitWatts"] is not None),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read EnvironmentMetrics linked from chassis, processors, and memory.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the underlying queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult whose data holds a summary and the per-resource metric rows.
        """
        rows = []
        seen = set()

        self._append_chassis_metrics(rows, seen, do_async=do_async)
        self._append_system_collection_metrics(
            rows,
            seen,
            "Processors",
            "Processor",
            do_async=do_async,
        )
        self._append_system_collection_metrics(
            rows,
            seen,
            "Memory",
            "Memory",
            do_async=do_async,
        )

        data = {
            "summary": self._summary(rows),
            "metrics": rows,
        }
        return CommandResult(data, None, None, None)
