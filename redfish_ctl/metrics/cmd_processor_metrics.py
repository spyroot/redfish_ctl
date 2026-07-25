"""Read Redfish ProcessorMetrics resources.

    redfish_ctl processor-metrics
    redfish_ctl processor-metrics --filename processors.json
"""

from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi
from .common import link, members, nvidia_oem, resource_id


class ProcessorMetrics(IDracManager,
                       scm_type=ApiRequestType.ProcessorMetrics,
                       name="processor-metrics",
                       metaclass=Singleton):
    """Read Processor Metrics linked from ComputerSystem processors."""

    def __init__(self, *args, **kwargs):
        """Initialize the processor-metrics command."""
        super(ProcessorMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``processor-metrics`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return (
            cmd_parser,
            "processor-metrics",
            "command read ProcessorMetrics resources",
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
    def _core_voltage(metrics):
        """Return the ``CoreVoltage`` block of a ProcessorMetrics resource.

        :param metrics: parsed ProcessorMetrics resource, or any value.
        :return: the ``CoreVoltage`` dict, or ``None`` when absent.
        """
        value = metrics.get("CoreVoltage") if isinstance(metrics, dict) else None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _row(system_uri, processor_uri, processor, metrics_uri, metrics):
        """Build a single ProcessorMetrics row.

        :param system_uri: URI of the owning ComputerSystem.
        :param processor_uri: URI of the Processor resource.
        :param processor: parsed Processor resource.
        :param metrics_uri: URI of the ProcessorMetrics resource.
        :param metrics: parsed ProcessorMetrics resource.
        :return: dict describing the processor and its metrics.
        """
        processor_id = processor.get("Id") or resource_id(processor_uri)
        return {
            "SystemId": resource_id(system_uri),
            "ProcessorId": processor_id,
            "ProcessorUri": processor_uri,
            "MetricsUri": metrics.get("@odata.id", metrics_uri),
            "Name": metrics.get("Name"),
            "BandwidthPercent": metrics.get("BandwidthPercent"),
            "OperatingSpeedMHz": metrics.get("OperatingSpeedMHz"),
            "CoreVoltage": ProcessorMetrics._core_voltage(metrics),
            "PowerLimitThrottleDuration": metrics.get(
                "PowerLimitThrottleDuration"
            ),
            "ThermalLimitThrottleDuration": metrics.get(
                "ThermalLimitThrottleDuration"
            ),
            "PCIeErrors": metrics.get("PCIeErrors"),
            "CacheMetricsTotal": metrics.get("CacheMetricsTotal"),
            "Nvidia": nvidia_oem(metrics),
        }

    @staticmethod
    def _summary(systems_count, processors_count, rows):
        """Summarize counts across the collected ProcessorMetrics rows.

        :param systems_count: number of systems that contributed processors.
        :param processors_count: total number of processors examined.
        :param rows: collected ProcessorMetrics rows.
        :return: dict of aggregate counts across the metric rows.
        """
        return {
            "systems": systems_count,
            "processors": processors_count,
            "metrics": len(rows),
            "cpu_metrics": sum(
                1 for row in rows if row["ProcessorId"].startswith("CPU_")
            ),
            "gpu_metrics": sum(
                1 for row in rows if row["ProcessorId"].startswith("GPU_")
            ),
            "bandwidth_percent": sum(
                1 for row in rows if row["BandwidthPercent"] is not None
            ),
            "core_voltage": sum(
                1 for row in rows if row["CoreVoltage"] is not None
            ),
            "pcie_error_blocks": sum(
                1 for row in rows if row["PCIeErrors"] is not None
            ),
            "nvidia_oem_metrics": sum(
                1 for row in rows if row["Nvidia"] is not None
            ),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk Systems -> Processors -> Metrics links.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the processor-metrics summary and metric rows.
        """
        rows = []
        systems_count = 0
        processors_count = 0

        systems = self._query_optional(RedfishApi.Systems, do_async=do_async)
        system_uris = members(systems)
        for system_uri in system_uris:
            system = self._query_optional(system_uri, do_async=do_async)
            processors_uri = link(system, "Processors")
            if not processors_uri:
                continue
            processors = self._query_optional(processors_uri, do_async=do_async)
            processor_uris = members(processors)
            if processor_uris:
                systems_count += 1
            processors_count += len(processor_uris)
            for processor_uri in processor_uris:
                processor = self._query_optional(
                    processor_uri, do_async=do_async)
                metrics_uri = link(processor, "Metrics")
                if not metrics_uri:
                    continue
                metrics = self._query_optional(metrics_uri, do_async=do_async)
                if not isinstance(metrics, dict) or not metrics:
                    continue
                rows.append(self._row(
                    system_uri,
                    processor_uri,
                    processor,
                    metrics_uri,
                    metrics,
                ))

        data = {
            "summary": self._summary(
                systems_count,
                processors_count,
                rows,
            ),
            "metrics": rows,
        }
        return CommandResult(data, None, None, None)
