"""Read Redfish ProcessorMetrics resources."""

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
        super(ProcessorMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``processor-metrics`` subcommand."""
        cmd_parser = cls.base_parser()
        return (
            cmd_parser,
            "processor-metrics",
            "command read ProcessorMetrics resources",
        )

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _core_voltage(metrics):
        value = metrics.get("CoreVoltage") if isinstance(metrics, dict) else None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _row(system_uri, processor_uri, processor, metrics_uri, metrics):
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
        """Walk Systems -> Processors -> Metrics links."""
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
