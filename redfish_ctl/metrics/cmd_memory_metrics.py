"""Read Redfish MemoryMetrics resources.

    redfish_ctl memory-metrics
    redfish_ctl memory-metrics --filename memory.json
"""

from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi
from .common import link, members, nvidia_oem, resource_id


class MemoryMetrics(IDracManager,
                    scm_type=ApiRequestType.MemoryMetrics,
                    name="memory-metrics",
                    metaclass=Singleton):
    """Read Memory Metrics linked from Memory and Processor MemorySummary."""

    def __init__(self, *args, **kwargs):
        """Initialize the memory-metrics command."""
        super(MemoryMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``memory-metrics`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return (
            cmd_parser,
            "memory-metrics",
            "command read MemoryMetrics resources",
        )

    def _query_optional(self, uri, do_async=False):
        """Query a Redfish URI, returning an empty dict on any error.

        :param uri: Redfish resource URI to fetch.
        :param do_async: when True, issue the query asynchronously.
        :return: the parsed resource dict, or an empty dict when the query fails.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _row(parent_type, parent_id, parent_uri, metrics_uri, metrics):
        """Build a single MemoryMetrics row.

        :param parent_type: kind of parent resource (Memory or ProcessorMemorySummary).
        :param parent_id: id of the parent resource.
        :param parent_uri: URI of the parent resource.
        :param metrics_uri: URI of the MemoryMetrics resource.
        :param metrics: parsed MemoryMetrics resource.
        :return: dict describing the parent and its memory metrics.
        """
        return {
            "ParentType": parent_type,
            "ParentId": parent_id,
            "ParentUri": parent_uri,
            "MetricsUri": metrics.get("@odata.id", metrics_uri),
            "Name": metrics.get("Name"),
            "BandwidthPercent": metrics.get("BandwidthPercent"),
            "CapacityUtilizationPercent": metrics.get(
                "CapacityUtilizationPercent"
            ),
            "OperatingSpeedMHz": metrics.get("OperatingSpeedMHz"),
            "LifeTime": metrics.get("LifeTime"),
            "HealthData": metrics.get("HealthData"),
            "Nvidia": nvidia_oem(metrics),
        }

    @staticmethod
    def _summary(systems_count, memory_modules_count, summary_count, rows):
        """Summarize counts across the collected MemoryMetrics rows.

        :param systems_count: number of systems that contributed metrics.
        :param memory_modules_count: total number of memory modules examined.
        :param summary_count: number of processor memory-summary metrics found.
        :param rows: collected MemoryMetrics rows.
        :return: dict of aggregate counts across the metric rows.
        """
        return {
            "systems": systems_count,
            "memory_modules": memory_modules_count,
            "processor_memory_summaries": summary_count,
            "metrics": len(rows),
            "capacity_utilization": sum(
                1
                for row in rows
                if row["CapacityUtilizationPercent"] is not None
            ),
            "health_data": sum(
                1 for row in rows if row["HealthData"] is not None
            ),
            "lifetime": sum(
                1 for row in rows if row["LifeTime"] is not None
            ),
            "nvidia_oem_metrics": sum(
                1 for row in rows if row["Nvidia"] is not None
            ),
        }

    def _append_metric(self,
                       rows,
                       seen,
                       parent_type,
                       parent_id,
                       parent_uri,
                       metrics_uri,
                       do_async=False):
        """Fetch a MemoryMetrics resource and append its row when new and non-empty.

        :param rows: list of rows to append to.
        :param seen: set of metrics URIs already collected, updated in place.
        :param parent_type: kind of parent resource for the row.
        :param parent_id: id of the parent resource.
        :param parent_uri: URI of the parent resource.
        :param metrics_uri: URI of the MemoryMetrics resource to fetch.
        :param do_async: when True, issue the query asynchronously.
        """
        if not metrics_uri or metrics_uri in seen:
            return
        metrics = self._query_optional(metrics_uri, do_async=do_async)
        if not isinstance(metrics, dict) or not metrics:
            return
        seen.add(metrics_uri)
        rows.append(self._row(
            parent_type,
            parent_id,
            parent_uri,
            metrics_uri,
            metrics,
        ))

    def _append_memory_metrics(self,
                               rows,
                               seen,
                               system,
                               do_async=False):
        """Append MemoryMetrics rows for the Memory modules of a system.

        :param rows: list of rows to append to.
        :param seen: set of metrics URIs already collected, updated in place.
        :param system: parsed ComputerSystem resource.
        :param do_async: when True, issue the queries asynchronously.
        :return: number of memory modules examined.
        """
        memory_count = 0
        memory_uri = link(system, "Memory")
        if not memory_uri:
            return memory_count

        memory = self._query_optional(memory_uri, do_async=do_async)
        memory_uris = members(memory)
        memory_count += len(memory_uris)
        for member_uri in memory_uris:
            member = self._query_optional(member_uri, do_async=do_async)
            member_id = member.get("Id") or resource_id(member_uri)
            self._append_metric(
                rows,
                seen,
                "Memory",
                member_id,
                member_uri,
                link(member, "Metrics"),
                do_async=do_async,
            )
        return memory_count

    def _append_processor_summary_metrics(self,
                                          rows,
                                          seen,
                                          system,
                                          do_async=False):
        """Append processor MemorySummary metric rows for a system.

        :param rows: list of rows to append to.
        :param seen: set of metrics URIs already collected, updated in place.
        :param system: parsed ComputerSystem resource.
        :param do_async: when True, issue the queries asynchronously.
        :return: number of processor memory-summary metrics appended.
        """
        summary_count = 0
        processors_uri = link(system, "Processors")
        if not processors_uri:
            return summary_count

        processors = self._query_optional(processors_uri, do_async=do_async)
        for processor_uri in members(processors):
            processor = self._query_optional(processor_uri, do_async=do_async)
            memory_summary = processor.get("MemorySummary")
            if not isinstance(memory_summary, dict):
                continue
            metrics_uri = link(memory_summary, "Metrics")
            if not metrics_uri:
                continue
            summary_count += 1
            processor_id = processor.get("Id") or resource_id(processor_uri)
            self._append_metric(
                rows,
                seen,
                "ProcessorMemorySummary",
                processor_id,
                processor_uri,
                metrics_uri,
                do_async=do_async,
            )
        return summary_count

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk Systems -> Memory Metrics and Processor MemorySummary Metrics.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the memory-metrics summary and metric rows.
        """
        rows = []
        seen = set()
        systems_count = 0
        memory_modules_count = 0
        summary_count = 0

        systems = self._query_optional(RedfishApi.Systems, do_async=do_async)
        system_uris = members(systems)
        for system_uri in system_uris:
            system = self._query_optional(system_uri, do_async=do_async)
            before_count = len(rows)
            memory_modules_count += self._append_memory_metrics(
                rows,
                seen,
                system,
                do_async=do_async,
            )
            summary_count += self._append_processor_summary_metrics(
                rows,
                seen,
                system,
                do_async=do_async,
            )
            if len(rows) > before_count:
                systems_count += 1

        data = {
            "summary": self._summary(
                systems_count,
                memory_modules_count,
                summary_count,
                rows,
            ),
            "metrics": rows,
        }
        return CommandResult(data, None, None, None)
