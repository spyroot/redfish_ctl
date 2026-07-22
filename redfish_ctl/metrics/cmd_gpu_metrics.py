"""Read consolidated Redfish GPU metric rows.

    redfish_ctl gpu-metrics
    redfish_ctl gpu-metrics --filename gpus.json
"""

from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi
from ..telemetry.exporter import (
    GPU_COMPUTE_PROPERTIES,
    GPU_MEMORY_ECC_PROPERTIES,
    GPU_MEMORY_ROW_REMAP_PROPERTIES,
    GPU_THROTTLE_PROPERTIES,
    _as_float,
    _duration_seconds,
)
from .common import link, members, nvidia_oem, resource_id


class GpuMetrics(IDracManager,
                 scm_type=ApiRequestType.GpuMetrics,
                 name="gpu-metrics",
                 metaclass=Singleton):
    """Read per-GPU ProcessorMetrics, Sensor, and MemoryMetrics links."""

    def __init__(self, *args, **kwargs):
        """Initialize the gpu-metrics command."""
        super(GpuMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``gpu-metrics`` subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        return (
            cmd_parser,
            "gpu-metrics",
            "command read GPU metric rows",
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
    def _odata_ids(data, key):
        """Collect ``@odata.id`` URIs from a link that may be single or a list.

        :param data: parsed Redfish resource, or any value.
        :param key: name of the link property to read.
        :return: list of ``@odata.id`` URIs; empty when none are present.
        """
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict):
            odata_id = value.get("@odata.id")
            return [odata_id] if isinstance(odata_id, str) else []
        if isinstance(value, list):
            return [
                item["@odata.id"]
                for item in value
                if isinstance(item, dict)
                and isinstance(item.get("@odata.id"), str)
            ]
        return []

    @staticmethod
    def _links(data):
        """Return the ``Links`` block of a Redfish resource.

        :param data: parsed Redfish resource, or any value.
        :return: the ``Links`` dict, or an empty dict when absent.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        return links if isinstance(links, dict) else {}

    @staticmethod
    def _status(data):
        """Extract present Health, HealthRollup, and State keys from ``Status``.

        :param data: parsed Redfish resource, or any value.
        :return: dict of any present status keys, or None when there is no Status block.
        """
        status = data.get("Status") if isinstance(data, dict) else None
        if not isinstance(status, dict):
            return None
        return {
            key: status[key]
            for key in ("Health", "HealthRollup", "State")
            if key in status
        }

    @staticmethod
    def _is_gpu(processor, processor_uri):
        """Decide whether a processor resource represents a GPU.

        :param processor: parsed Processor resource.
        :param processor_uri: URI of the processor, used to derive its id.
        :return: True when the processor is a GPU, False otherwise.
        """
        processor_id = processor.get("Id") or resource_id(processor_uri)
        return (
            processor.get("ProcessorType") == "GPU"
            or str(processor_id).startswith("GPU_")
        )

    @staticmethod
    def _clock_mhz(processor, metrics):
        """Collect base, min, max, operating, and limit clock speeds for a GPU.

        :param processor: parsed Processor resource.
        :param metrics: parsed ProcessorMetrics resource.
        :return: dict of clock speeds keyed base/min/max/operating/speed_limit.
        """
        return {
            "base": processor.get("BaseSpeedMHz"),
            "min": processor.get("MinSpeedMHz"),
            "max": processor.get("MaxSpeedMHz"),
            "operating": (
                metrics.get("OperatingSpeedMHz")
                or processor.get("OperatingSpeedMHz")
            ),
            "speed_limit": processor.get("SpeedLimitMHz"),
        }

    @staticmethod
    def _compute_utilization(metrics):
        """Read NVIDIA OEM compute-utilization percentages from GPU metrics.

        :param metrics: parsed ProcessorMetrics resource.
        :return: dict of labeled utilization values that parse as floats.
        """
        nvidia = nvidia_oem(metrics) or {}
        values = {}
        for key, label in GPU_COMPUTE_PROPERTIES.items():
            raw_value = nvidia.get(key)
            if isinstance(raw_value, list):
                continue
            value = _as_float(raw_value)
            if value is not None:
                values[label] = value
        return values

    @staticmethod
    def _throttle_durations(metrics):
        """Read GPU throttle durations as seconds from metrics or NVIDIA OEM data.

        :param metrics: parsed ProcessorMetrics resource.
        :return: dict of labeled throttle durations in seconds.
        """
        nvidia = nvidia_oem(metrics) or {}
        values = {}
        for key, label in GPU_THROTTLE_PROPERTIES.items():
            raw_value = (
                metrics.get(key)
                if key in metrics
                else nvidia.get(key)
            )
            seconds = _duration_seconds(raw_value)
            if seconds is not None:
                values[label] = seconds
        return values

    @staticmethod
    def _ecc_errors(metrics):
        """Extract lifetime ECC error counts from memory metrics.

        :param metrics: parsed MemoryMetrics resource.
        :return: dict of labeled ECC counts, or empty when no ``LifeTime`` block exists.
        """
        lifetime = metrics.get("LifeTime") if isinstance(metrics, dict) else None
        if not isinstance(lifetime, dict):
            return {}
        return {
            label: lifetime[key]
            for key, label in GPU_MEMORY_ECC_PROPERTIES.items()
            if key in lifetime
        }

    @staticmethod
    def _row_remapping(metrics):
        """Extract NVIDIA row-remapping fields from memory metrics.

        :param metrics: parsed MemoryMetrics resource.
        :return: dict of labeled row-remapping values plus extra keys, or empty when absent.
        """
        nvidia = nvidia_oem(metrics) or {}
        remapping = nvidia.get("RowRemapping")
        if not isinstance(remapping, dict):
            return {}
        return {
            label: remapping[key]
            for key, label in GPU_MEMORY_ROW_REMAP_PROPERTIES.items()
            if key in remapping
        } | {
            key: value
            for key, value in remapping.items()
            if key not in GPU_MEMORY_ROW_REMAP_PROPERTIES
        }

    def _temperature_readings(self, processor_uri, processor, do_async=False):
        """Collect GPU temperature sensor readings linked through its chassis.

        :param processor_uri: URI of the GPU processor being matched.
        :param processor: parsed Processor resource.
        :param do_async: when True, issue the chassis and sensor queries asynchronously.
        :return: dict mapping sensor id to temperature reading in Celsius.
        """
        links = self._links(processor)
        chassis_ids = self._odata_ids(links, "Chassis")
        readings = {}
        for chassis_uri in chassis_ids:
            chassis = self._query_optional(chassis_uri, do_async=do_async)
            sensors_uri = link(chassis, "Sensors")
            if not sensors_uri:
                continue
            sensors = self._query_optional(sensors_uri, do_async=do_async)
            for sensor_uri in members(sensors):
                sensor = self._query_optional(sensor_uri, do_async=do_async)
                if sensor.get("ReadingType") != "Temperature":
                    continue
                if processor_uri not in self._odata_ids(sensor, "RelatedItem"):
                    continue
                value = _as_float(sensor.get("Reading"))
                if value is None:
                    continue
                sensor_id = sensor.get("Id") or resource_id(sensor_uri)
                readings[sensor_id] = value
        return readings

    def _memory_metric_row(self, memory_uri, memory, metrics_uri, metrics):
        """Build a single linked-memory metric row for a GPU.

        :param memory_uri: URI of the Memory resource.
        :param memory: parsed Memory resource.
        :param metrics_uri: URI of the MemoryMetrics resource.
        :param metrics: parsed MemoryMetrics resource.
        :return: dict describing the memory module and its metrics.
        """
        memory_oem = nvidia_oem(memory) or {}
        return {
            "MemoryId": memory.get("Id") or resource_id(memory_uri),
            "MemoryUri": memory_uri,
            "MetricsUri": metrics.get("@odata.id", metrics_uri),
            "MemoryType": memory.get("MemoryType"),
            "MemoryDeviceType": memory.get("MemoryDeviceType"),
            "CapacityMiB": memory.get("CapacityMiB"),
            "BandwidthPercent": metrics.get("BandwidthPercent"),
            "CapacityUtilizationPercent": metrics.get(
                "CapacityUtilizationPercent"
            ),
            "OperatingSpeedMHz": (
                metrics.get("OperatingSpeedMHz")
                or memory.get("OperatingSpeedMhz")
                or memory.get("OperatingSpeedMHz")
            ),
            "EccErrors": self._ecc_errors(metrics),
            "RowRemapping": self._row_remapping(metrics),
            "RowRemappingFailed": memory_oem.get("RowRemappingFailed"),
            "RowRemappingPending": memory_oem.get("RowRemappingPending"),
            "Status": self._status(memory),
        }

    def _linked_memory_metrics(self, processor, do_async=False):
        """Collect memory-metric rows for the Memory linked from a GPU processor.

        :param processor: parsed Processor resource.
        :param do_async: when True, issue the memory and metrics queries asynchronously.
        :return: list of memory metric rows; empty when none are linked.
        """
        rows = []
        links = self._links(processor)
        for memory_uri in self._odata_ids(links, "Memory"):
            memory = self._query_optional(memory_uri, do_async=do_async)
            metrics_uri = link(memory, "Metrics")
            if not metrics_uri:
                continue
            metrics = self._query_optional(metrics_uri, do_async=do_async)
            if not isinstance(metrics, dict) or not metrics:
                continue
            rows.append(
                self._memory_metric_row(
                    memory_uri,
                    memory,
                    metrics_uri,
                    metrics,
                )
            )
        return rows

    def _summary_memory_metrics(self, processor, do_async=False):
        """Read the aggregated MemorySummary metrics for a GPU processor.

        :param processor: parsed Processor resource.
        :param do_async: when True, issue the metrics query asynchronously.
        :return: dict of summary memory metrics, or None when none are linked.
        """
        memory_summary = processor.get("MemorySummary")
        if not isinstance(memory_summary, dict):
            return None
        metrics_uri = link(memory_summary, "Metrics")
        if not metrics_uri:
            return None
        metrics = self._query_optional(metrics_uri, do_async=do_async)
        if not isinstance(metrics, dict) or not metrics:
            return None
        return {
            "MetricsUri": metrics.get("@odata.id", metrics_uri),
            "TotalMemorySizeMiB": memory_summary.get("TotalMemorySizeMiB"),
            "ECCModeEnabled": memory_summary.get("ECCModeEnabled"),
            "BandwidthPercent": metrics.get("BandwidthPercent"),
            "CapacityUtilizationPercent": metrics.get(
                "CapacityUtilizationPercent"
            ),
            "OperatingSpeedMHz": metrics.get("OperatingSpeedMHz"),
            "EccErrors": self._ecc_errors(metrics),
            "RowRemapping": self._row_remapping(metrics),
        }

    def _gpu_row(self, system_uri, processor_uri, processor, do_async=False):
        """Build the consolidated metric row for a single GPU processor.

        :param system_uri: URI of the owning ComputerSystem.
        :param processor_uri: URI of the GPU processor.
        :param processor: parsed Processor resource.
        :param do_async: when True, issue linked-resource queries asynchronously.
        :return: dict of GPU identity, status, clocks, temperatures, and metrics.
        """
        metrics_uri = link(processor, "Metrics")
        metrics = (
            self._query_optional(metrics_uri, do_async=do_async)
            if metrics_uri
            else {}
        )
        if not isinstance(metrics, dict):
            metrics = {}
        gpu_id = processor.get("Id") or resource_id(processor_uri)
        operating_speed = (
            metrics.get("OperatingSpeedMHz")
            or processor.get("OperatingSpeedMHz")
        )
        return {
            "SystemId": resource_id(system_uri),
            "GpuId": gpu_id,
            "ProcessorUri": processor_uri,
            "ProcessorMetricsUri": (
                metrics.get("@odata.id", metrics_uri)
                if metrics_uri
                else None
            ),
            "Name": processor.get("Name"),
            "Model": processor.get("Model"),
            "Manufacturer": processor.get("Manufacturer"),
            "FirmwareVersion": processor.get("FirmwareVersion"),
            "Status": self._status(processor),
            "OperatingSpeedMHz": operating_speed,
            "ClockMHz": self._clock_mhz(processor, metrics),
            "TemperaturesCelsius": self._temperature_readings(
                processor_uri,
                processor,
                do_async=do_async,
            ),
            "ComputeUtilizationPercent": self._compute_utilization(metrics),
            "ThrottleDurationSeconds": self._throttle_durations(metrics),
            "ProcessorMetrics": {
                "BandwidthPercent": metrics.get("BandwidthPercent"),
                "OperatingSpeedMHz": metrics.get("OperatingSpeedMHz"),
                "CoreVoltage": metrics.get("CoreVoltage"),
                "PCIeErrors": metrics.get("PCIeErrors"),
                "CacheMetricsTotal": metrics.get("CacheMetricsTotal"),
            },
            "Memory": self._linked_memory_metrics(
                processor,
                do_async=do_async,
            ),
            "MemorySummaryMetrics": self._summary_memory_metrics(
                processor,
                do_async=do_async,
            ),
        }

    @staticmethod
    def _summary(systems_count, processors_count, rows):
        """Summarize counts across the collected GPU rows.

        :param systems_count: number of systems that contributed processors.
        :param processors_count: total number of processors examined.
        :param rows: collected GPU metric rows.
        :return: dict of aggregate counts across the GPU rows.
        """
        return {
            "systems": systems_count,
            "processors": processors_count,
            "gpus": len(rows),
            "temperature_sensors": sum(
                len(row["TemperaturesCelsius"]) for row in rows
            ),
            "memory_metrics": sum(len(row["Memory"]) for row in rows),
            "summary_memory_metrics": sum(
                1 for row in rows if row["MemorySummaryMetrics"]
            ),
            "compute_fields": sum(
                len(row["ComputeUtilizationPercent"]) for row in rows
            ),
            "throttle_fields": sum(
                len(row["ThrottleDurationSeconds"]) for row in rows
            ),
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Walk Systems -> GPU Processors and linked GPU metric resources.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: when True, issue the Redfish queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the GPU summary and per-GPU metric rows.
        """
        rows = []
        systems_count = 0
        processors_count = 0

        systems = self._query_optional(RedfishApi.Systems, do_async=do_async)
        for system_uri in members(systems):
            system = self._query_optional(system_uri, do_async=do_async)
            processors_uri = link(system, "Processors")
            if not processors_uri:
                continue
            processors = self._query_optional(
                processors_uri,
                do_async=do_async,
            )
            processor_uris = members(processors)
            if processor_uris:
                systems_count += 1
            processors_count += len(processor_uris)
            for processor_uri in processor_uris:
                processor = self._query_optional(
                    processor_uri,
                    do_async=do_async,
                )
                if not self._is_gpu(processor, processor_uri):
                    continue
                rows.append(
                    self._gpu_row(
                        system_uri,
                        processor_uri,
                        processor,
                        do_async=do_async,
                    )
                )

        rows.sort(key=lambda row: (row["SystemId"], row["GpuId"]))
        data = {
            "summary": self._summary(systems_count, processors_count, rows),
            "gpus": rows,
        }
        return CommandResult(data, None, None, None)
