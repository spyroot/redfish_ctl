"""Read consolidated Redfish GPU metric rows."""

from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
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


class GpuMetrics(RedfishManagerBase,
                 scm_type=ApiRequestType.GpuMetrics,
                 name="gpu-metrics",
                 metaclass=Singleton):
    """Read per-GPU ProcessorMetrics, Sensor, and MemoryMetrics links."""

    def __init__(self, *args, **kwargs):
        super(GpuMetrics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``gpu-metrics`` subcommand."""
        cmd_parser = cls.base_parser()
        return (
            cmd_parser,
            "gpu-metrics",
            "command read GPU metric rows",
        )

    def _query_optional(self, uri, do_async=False):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _odata_ids(data, key):
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
        links = data.get("Links") if isinstance(data, dict) else None
        return links if isinstance(links, dict) else {}

    @staticmethod
    def _status(data):
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
        processor_id = processor.get("Id") or resource_id(processor_uri)
        return (
            processor.get("ProcessorType") == "GPU"
            or str(processor_id).startswith("GPU_")
        )

    @staticmethod
    def _clock_mhz(processor, metrics):
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
        """Walk Systems -> GPU Processors and linked GPU metric resources."""
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
