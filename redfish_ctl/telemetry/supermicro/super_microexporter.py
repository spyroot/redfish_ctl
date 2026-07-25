"""Supermicro/GB300 telemetry data-adaptation for the Redfish exporter.

Every vendor exporter emits the same ``hw.*`` metric contract; the only
per-vendor delta is that each BMC exposes the source data differently. This
module is the Supermicro/GB300 **data adapter**: it maps this vendor's Redfish
row shapes (Chassis EnvironmentMetrics, Sensors, nvlink-ports, TelemetryService
MetricReports, ThermalSubsystem, LeakDetectors, NetworkAdapters,
ComponentIntegrity) into the shared, vendor-neutral :class:`MetricSample` model
defined in :mod:`redfish_ctl.telemetry.exporter`.

The generic sample/dimension/coercion primitives (``_sample``, ``_with_dims``,
``_dim_value``, ``_as_float``, ``_duration_seconds``, ``_reading``,
``_unit_for_metric``) and the metric catalog live in the shared exporter module
and are imported here; nothing Supermicro-specific belongs in that shared layer.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

from redfish_ctl.telemetry.exporter import (
    AbstractExporterReader,
    MetricSample,
    _as_float,
    _dim_value,
    _duration_seconds,
    _reading,
    _sample,
    _unit_for_metric,
    _with_dims,
)

# Supermicro/GB300 source-property → metric maps. These name vendor Redfish
# properties, so they are vendor data, not part of the shared contract.
SENSOR_METRIC = {
    "Temperature": ("hw.temperature", "sensor"),
    "Rotational": ("hw.fan_speed", "fan"),
    "Voltage": ("hw.voltage", "sensor"),
}
FABRIC_PROPERTY_METRICS = {
    "BitErrorRate": "hw.fabric.bit_error_rate",
    "CurrentSpeedGbps": "hw.fabric.port_speed",
    "CRCErrorCount": "hw.fabric.crc_errors",
    "EffectiveBER": "hw.fabric.effective_ber",
    "EffectiveError": "hw.fabric.effective_errors",
    "FECErrorCount": "hw.fabric.fec_errors",
    "IntentionalLinkDownCount": "hw.fabric.intentional_link_down_count",
    "LinkDownedCount": "hw.fabric.link_down_count",
    "LinkErrorRecoveryCount": "hw.fabric.link_error_recovery_count",
    "MalformedPackets": "hw.fabric.malformed_packets",
    "NVLinkDataRxBandwidthGbps": "hw.fabric.rx_gbps",
    "NVLinkDataTxBandwidthGbps": "hw.fabric.tx_gbps",
    "NVLinkRawRxBandwidthGbps": "hw.fabric.raw_rx_gbps",
    "NVLinkRawTxBandwidthGbps": "hw.fabric.raw_tx_gbps",
    "RXBytes": "hw.fabric.rx_bytes",
    "RXErrors": "hw.fabric.rx_errors",
    "RXFrames": "hw.fabric.rx_frames",
    "RXNoProtocolBytes": "hw.fabric.rx_no_protocol_bytes",
    "RXRemotePhysicalErrors": "hw.fabric.rx_remote_physical_errors",
    "RXSwitchRelayErrors": "hw.fabric.rx_switch_relay_errors",
    "SymbolErrors": "hw.fabric.symbol_errors",
    "TXBytes": "hw.fabric.tx_bytes",
    "TXDiscards": "hw.fabric.tx_discards",
    "TXFrames": "hw.fabric.tx_frames",
    "TXNoProtocolBytes": "hw.fabric.tx_no_protocol_bytes",
    "TXWait": "hw.fabric.tx_wait",
    "TotalRawBER": "hw.fabric.raw_ber",
    "TotalRawError": "hw.fabric.raw_errors",
    "UnintentionalLinkDownCount": "hw.fabric.unintentional_link_down_count",
    "VL15Dropped": "hw.fabric.vl15_dropped",
    "VL15TXBytes": "hw.fabric.vl15_tx_bytes",
    "VL15TXPackets": "hw.fabric.vl15_tx_packets",
}
GPU_COMPUTE_PROPERTIES = {
    "DMMAUtilizationPercent": "dmma",
    "FP16ActivityPercent": "fp16_activity",
    "FP32ActivityPercent": "fp32_activity",
    "FP64ActivityPercent": "fp64_activity",
    "GraphicsEngineActivityPercent": "graphics_engine_activity",
    "HMMAUtilizationPercent": "hmma",
    "IMMAUtilizationPercent": "imma",
    "IntegerActivityUtilizationPercent": "integer_activity",
    "NVDecInstanceUtilizationPercent": "nvdec_instance",
    "NVDecUtilizationPercent": "nvdec",
    "NVJpgInstanceUtilizationPercent": "nvjpg_instance",
    "NVJpgUtilizationPercent": "nvjpg",
    "NVOfaUtilizationPercent": "nvofa",
    "SMActivityPercent": "sm_activity",
    "SMOccupancyPercent": "sm_occupancy",
    "SMUtilizationPercent": "sm",
    "TensorCoreActivityPercent": "tensor_core_activity",
}
GPU_MEMORY_PROPERTIES = {
    "BandwidthPercent": ("hw.gpu.memory.bandwidth_utilization", "bandwidth", "%"),
    "CapacityUtilizationPercent": ("hw.gpu.memory.capacity_utilization", "capacity", "%"),
    "OperatingSpeedMHz": ("hw.gpu.memory.clock_mhz", "operating_speed", "MHz"),
}
GPU_MEMORY_ECC_PROPERTIES = {
    "CorrectableECCErrorCount": "correctable",
    "UncorrectableECCErrorCount": "uncorrectable",
}
GPU_MEMORY_ROW_REMAP_PROPERTIES = {
    "CorrectableRowRemappingCount": "correctable",
    "HighAvailabilityBankCount": "high_availability",
    "LowAvailabilityBankCount": "low_availability",
    "MaxAvailabilityBankCount": "max_availability",
    "NoAvailabilityBankCount": "no_availability",
    "PartialAvailabilityBankCount": "partial_availability",
    "UncorrectableRowRemappingCount": "uncorrectable",
}
GPU_THROTTLE_PROPERTIES = {
    "GlobalSoftwareViolationThrottleDuration": "global_software_violation",
    "HardwareViolationThrottleDuration": "hardware_violation",
    "PowerLimitThrottleDuration": "power_limit",
    "ThermalLimitThrottleDuration": "thermal_limit",
}
# One-hot state-metric label allowlists (specs/telemetry/gates.md, M1): each
# categorical row emits value 1 with a normalized lowercase label; values
# outside the allowlist map to "unknown" (health/state) or "other"
# (reason/reset_type) — never dropped, never free-form.
HEALTH_LABELS = {"ok", "warning", "critical"}
STATE_LABELS = {"enabled", "disabled", "standby_offline", "standby_spare",
                "in_test", "starting", "absent", "unavailable_offline",
                "deferring", "quiesced", "updating", "qualified"}
LINK_DOWN_REASONS = {"peer_reset_event"}
RESET_TYPES = {"pf_flr", "conventional", "fundamental"}
EDP_STATES = {"normal", "asserted"}
POWER_BREAK_STATES = {"normal", "active"}


def build_metric_samples(
        identity: Mapping[str, str],
        environment_rows: Iterable[Mapping],
        sensor_rows: Iterable[Mapping],
        nvlink_rows: Iterable[Mapping],
        metric_report_rows: Iterable[Mapping],
        thermal_rows: Iterable[Mapping] = (),
        leak_detection_rows: Iterable[Mapping] = (),
        network_rows: Iterable[Mapping] = (),
        component_integrity_rows: Iterable[Mapping] = ()) -> list[MetricSample]:
    """Build exporter samples from normalized Supermicro Redfish command rows.

    :param identity: fixed join dimensions applied to every sample.
    :param environment_rows: Chassis EnvironmentMetrics rows (power/energy/fan).
    :param sensor_rows: Redfish Sensor rows (thermal/fan/voltage/power).
    :param nvlink_rows: nvlink-ports rows for per-link fabric metrics.
    :param metric_report_rows: TelemetryService MetricReport rows.
    :param thermal_rows: ThermalSubsystem temperature rows.
    :param leak_detection_rows: LeakDetector rows.
    :param network_rows: NIC/DPU network-adapter inventory rows.
    :param component_integrity_rows: ComponentIntegrity rows.
    :return: combined list of MetricSample objects from all row sources.
    """
    samples: list[MetricSample] = []
    samples.extend(samples_from_environment_rows(environment_rows, identity))
    samples.extend(samples_from_sensor_rows(sensor_rows, identity))
    samples.extend(samples_from_nvlink_rows(nvlink_rows, identity))
    samples.extend(samples_from_thermal_rows(thermal_rows, identity))
    samples.extend(samples_from_metric_report_rows(metric_report_rows, identity))
    samples.extend(samples_from_leak_detection_rows(leak_detection_rows, identity))
    samples.extend(samples_from_network_rows(network_rows, identity))
    samples.extend(samples_from_component_integrity_rows(component_integrity_rows, identity))
    return samples


def samples_from_environment_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map Chassis EnvironmentMetrics rows into chassis/GPU power metrics.

    :param rows: Chassis EnvironmentMetrics rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: power, energy and fan-speed samples derived from the rows.
    """
    samples = []
    for row in rows:
        chassis = _environment_chassis(row)
        dims = _environment_dims(identity, row, chassis)
        gpu = _environment_gpu(row, chassis)
        power = _as_float(_reading(row.get("PowerWatts")))
        if power is not None:
            metric = "hw.gpu.power" if gpu and row.get("ParentType") != "Memory" else "hw.power"
            samples.append(_sample(metric, power, dims | ({"gpu": gpu} if gpu else {}), unit="W"))
        energy = _as_float(_reading(row.get("EnergykWh") or row.get("EnergyKWh")))
        if energy is not None:
            samples.append(_sample(
                "hw.energy_kwh",
                energy,
                dims | ({"gpu": gpu} if gpu else {}),
                unit="kWh",
            ))
        for fan_name, rpm in _fan_readings(row):
            samples.append(_sample("hw.fan_speed", rpm, dims | {"fan": _dim_value(fan_name)}, "RPM"))
    return samples


def samples_from_sensor_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map Redfish Sensor rows into chassis thermal/fan/voltage/GPU power metrics.

    :param rows: Redfish Sensor rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: thermal, fan, voltage and power samples derived from the rows.
    """
    samples = []
    for row in rows:
        value = _as_float(row.get("Reading"))
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        reading_type = row.get("ReadingType")
        name = str(row.get("Name") or "sensor")
        dims = _with_dims(identity, source="sensor", chassis=chassis)
        health = row.get("Health")
        if health:
            dims["health"] = str(health)
        if reading_type == "Power" and _gpu_from_chassis(chassis):
            samples.append(_sample("hw.gpu.power", value, dims | _gpu_dim(chassis), "W"))
        elif reading_type == "Power":
            samples.append(_sample("hw.power", value, dims | {"sensor": _dim_value(name)}, "W"))
        elif reading_type in SENSOR_METRIC:
            metric, label = SENSOR_METRIC[reading_type]
            samples.append(_sample(metric, value, dims | {label: _dim_value(name)}, row.get("ReadingUnits")))
    return samples


def samples_from_nvlink_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map nvlink-ports rows into per-link fabric metrics.

    :param rows: nvlink-ports rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-link fabric samples (link state, speed, byte counters, BER).
    """
    samples = []
    for row in rows:
        dims = _fabric_dims(identity, row.get("System"), row.get("GPU"), row.get("Port"), "nvlink")
        link_up = 1.0 if row.get("LinkStatus") == "LinkUp" else 0.0
        samples.append(_sample("hw.fabric.link_up", link_up, dims, None))
        for key, metric, unit in (
                ("CurrentSpeedGbps", "hw.fabric.port_speed", "Gbps"),
                ("RXBytes", "hw.fabric.rx_bytes", "By"),
                ("TXBytes", "hw.fabric.tx_bytes", "By"),
                ("BitErrorRate", "hw.fabric.bit_error_rate", None)):
            value = _as_float(row.get(key))
            if value is not None:
                samples.append(_sample(metric, value, dims, unit))
    return samples


def samples_from_metric_report_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map every TelemetryService MetricReport row into a metric sample.

    Fabric properties get curated ``hw.fabric.*`` names and fabric dimensions;
    every other numeric property (GPU FP16/FP32 activity, thermal, power,
    memory, …) is emitted under a generic ``hw.gb300.*`` name so the FULL
    telemetry surface reaches OTel/Prometheus, not just the fabric subset.
    Categorical rows (Health, HealthRollup, State, LinkDownReasonCode,
    EDPViolationState, PowerBreakPerformanceState, LastResetType) are mapped
    to one-hot state samples by :func:`_state_enum_sample` per the M1 model
    in ``specs/telemetry/gates.md`` instead of being dropped.

    :param rows: TelemetryService MetricReport rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: one MetricSample per convertible MetricReport row.
    """
    samples = []
    for row in rows:
        prop = row.get("MetricProperty")
        if not prop:
            continue
        prop_info = _parse_metric_property(str(prop))
        prop_name = prop_info["property"]
        gpu_sample = _gpu_metric_report_sample(prop_info, row, identity)
        if gpu_sample is not None:
            samples.append(gpu_sample)
            continue

        value = _as_float(row.get("MetricValue"))
        if value is None:
            state_sample = _state_enum_sample(prop_info, row, identity)
            if state_sample is not None:
                samples.append(state_sample)
            continue
        if prop_name in FABRIC_PROPERTY_METRICS:
            metric = FABRIC_PROPERTY_METRICS[prop_name]
            fabric = "ib" if prop_info.get("port", "").lower().startswith("ib") else "nvlink"
            dims = _fabric_dims(identity, prop_info.get("system"),
                                prop_info.get("gpu"), prop_info.get("port"), fabric)
        else:
            metric = _generic_metric_name(prop_name)
            dims = _with_dims(identity, source="metric-report",
                              property=_dim_value(prop_name))
            for key in ("system", "gpu", "port", "chassis", "index"):
                if prop_info.get(key):
                    dims[key] = str(prop_info[key])
        dims["report"] = str(row.get("Report") or "unknown")
        samples.append(_sample(metric, value, dims, _unit_for_metric(metric), row.get("Timestamp")))
    return samples


def _state_label(text: str) -> str:
    """Normalize an enum string to a lowercase snake_case label value.

    :param text: raw vendor enum text (for example ``PeerResetEvent``).
    :return: normalized label (for example ``peer_reset_event``).
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    return snake or "unknown"


def _state_enum_sample(
        prop_info: Mapping[str, str],
        row: Mapping,
        identity: Mapping[str, str]) -> Optional[MetricSample]:
    """Map a categorical MetricReport row to a one-hot state sample.

    Implements the M1 model from ``specs/telemetry/gates.md``: every known
    state/health enum row emits value 1 with a normalized, allowlisted
    lowercase label — ``Health``/``HealthRollup`` → ``hw.component.health`` /
    ``hw.component.health_rollup`` (``health`` label), ``State`` →
    ``hw.component.state`` (``state`` label), ``LinkDownReasonCode`` →
    ``hw.fabric.link_down_reason`` (``reason`` label — the WHY behind
    link-down counters), ``EDPViolationState`` →
    ``hw.power.edp_violation_state``, ``PowerBreakPerformanceState`` →
    ``hw.power.break_performance_state``, ``LastResetType`` →
    ``hw.component.last_reset_type`` (``reset_type`` label). Values outside
    an allowlist map to ``unknown`` (health/state) or ``other``
    (reason/reset_type/power states) so no vendor string is ever dropped and
    no free-form text ever becomes a label.

    :param prop_info: parsed MetricProperty fields (property, system, gpu, port, …).
    :param row: the raw MetricReport row.
    :param identity: fixed join dimensions applied to the sample.
    :return: the mapped MetricSample, or None when the row is not a known
        categorical property (or its value is empty).
    """
    prop_name = prop_info["property"]
    text = str(row.get("MetricValue") or "").strip()
    if not text:
        return None
    label = _state_label(text)
    dims = _with_dims(identity, source="metric-report",
                      property=_dim_value(prop_name))
    for key in ("system", "gpu", "port", "chassis", "memory", "index"):
        if prop_info.get(key):
            dims[key] = str(prop_info[key])
    dims["report"] = str(row.get("Report") or "unknown")

    if prop_name in ("Health", "HealthRollup"):
        metric = ("hw.component.health" if prop_name == "Health"
                  else "hw.component.health_rollup")
        dims["health"] = label if label in HEALTH_LABELS else "unknown"
    elif prop_name == "State":
        metric = "hw.component.state"
        dims["state"] = label if label in STATE_LABELS else "unknown"
    elif prop_name == "LinkDownReasonCode":
        metric = "hw.fabric.link_down_reason"
        dims["reason"] = label if label in LINK_DOWN_REASONS else "other"
        dims["fabric"] = ("ib" if str(prop_info.get("port", "")).lower().startswith("ib")
                          else "nvlink")
    elif prop_name == "EDPViolationState":
        metric = "hw.power.edp_violation_state"
        dims["state"] = label if label in EDP_STATES else "other"
    elif prop_name == "PowerBreakPerformanceState":
        metric = "hw.power.break_performance_state"
        dims["state"] = label if label in POWER_BREAK_STATES else "other"
    elif prop_name == "LastResetType":
        metric = "hw.component.last_reset_type"
        dims["reset_type"] = label if label in RESET_TYPES else "other"
    else:
        return None
    return _sample(metric, 1.0, dims, None, row.get("Timestamp"))


def _gpu_metric_report_sample(
        prop_info: Mapping[str, str],
        row: Mapping,
        identity: Mapping[str, str]) -> Optional[MetricSample]:
    """Build a GPU-specific MetricSample from a MetricReport row, if applicable.

    :param prop_info: parsed MetricProperty fields (property, source, gpu, index, …).
    :param row: the raw MetricReport row.
    :param identity: fixed join dimensions applied to the sample.
    :return: a GPU temperature/clock/utilization/throttle/memory sample, or None
        when the row is not a recognized GPU metric.
    """
    prop_name = str(prop_info.get("property") or "")
    gpu = _gpu_from_metric_info(prop_info)
    if not gpu:
        return None

    source = prop_info.get("metric_source")
    value = _as_float(row.get("MetricValue"))
    dims = _gpu_metric_dims(identity, prop_info, row, gpu)

    if source == "sensor" and _is_gpu_temperature(prop_name):
        if value is None:
            return None
        return _sample(
            "hw.gpu.temperature",
            value,
            dims | {"property": "temperature", "sensor": _dim_value(prop_name)},
            "Cel",
            row.get("Timestamp"),
        )

    if source == "processor":
        if prop_name == "OperatingSpeedMHz" and value is not None:
            return _sample(
                "hw.gpu.clock_mhz",
                value,
                dims | {"property": "operating_speed"},
                "MHz",
                row.get("Timestamp"),
            )
        if prop_name in GPU_COMPUTE_PROPERTIES and value is not None:
            metric_dims = {
                "property": GPU_COMPUTE_PROPERTIES[prop_name],
            }
            if prop_info.get("index"):
                metric_dims["index"] = str(prop_info["index"])
            return _sample(
                "hw.gpu.compute.utilization",
                value,
                dims | metric_dims,
                "%",
                row.get("Timestamp"),
            )
        if prop_name in GPU_THROTTLE_PROPERTIES:
            seconds = _duration_seconds(row.get("MetricValue"))
            if seconds is None:
                return None
            return _sample(
                "hw.gpu.throttle.duration_seconds",
                seconds,
                dims | {"property": GPU_THROTTLE_PROPERTIES[prop_name]},
                "s",
                row.get("Timestamp"),
                metric_type="counter",  # cumulative throttle time, not a gauge
            )

    if source == "memory":
        if prop_name in GPU_MEMORY_PROPERTIES and value is not None:
            metric, property_name, unit = GPU_MEMORY_PROPERTIES[prop_name]
            return _sample(
                metric,
                value,
                dims | {"property": property_name},
                unit,
                row.get("Timestamp"),
            )
        if prop_name in GPU_MEMORY_ECC_PROPERTIES and value is not None:
            return _sample(
                "hw.gpu.memory.ecc_errors",
                value,
                dims | {"property": GPU_MEMORY_ECC_PROPERTIES[prop_name]},
                None,
                row.get("Timestamp"),
            )
        if prop_name in GPU_MEMORY_ROW_REMAP_PROPERTIES and value is not None:
            return _sample(
                "hw.gpu.memory.row_remap_count",
                value,
                dims | {"property": GPU_MEMORY_ROW_REMAP_PROPERTIES[prop_name]},
                None,
                row.get("Timestamp"),
            )
        if prop_name == "RowRemappingFailed" and value is not None:
            return _sample(
                "hw.gpu.memory.row_remapping_failed",
                value,
                dims | {"property": "row_remapping_failed"},
                None,
                row.get("Timestamp"),
            )

    return None


def _gpu_metric_dims(
        identity: Mapping[str, str],
        prop_info: Mapping[str, str],
        row: Mapping,
        gpu: str) -> dict[str, str]:
    """Build the GPU metric-report dimensions for one sample.

    :param identity: fixed join dimensions applied to the sample.
    :param prop_info: parsed MetricProperty fields providing system/chassis/memory context.
    :param row: the raw MetricReport row (supplies the report name).
    :param gpu: the resolved GPU identifier.
    :return: dimension mapping for the GPU sample.
    """
    dims = _with_dims(identity, source="metric-report", gpu=gpu)
    for key in ("system", "chassis", "memory"):
        if prop_info.get(key):
            dims[key] = str(prop_info[key])
    if row.get("Report"):
        dims["report"] = str(row["Report"])
    return dims


def samples_from_thermal_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map ThermalSubsystem temperature readings into per-zone metrics.

    :param rows: ThermalSubsystem temperature rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-zone ``hw.temperature`` samples derived from the rows.
    """
    samples = []
    for row in rows:
        reading = (row.get("ReadingCelsius")
                   if row.get("ReadingCelsius") is not None
                   else row.get("Reading"))
        value = _as_float(reading)
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        name = str(row.get("DeviceName") or row.get("Name")
                   or row.get("DataSourceUri") or "temperature").rsplit("/", 1)[-1]
        zone = row.get("PhysicalContext") or name
        dims = _with_dims(identity, source="thermal-subsystem",
                          chassis=chassis, sensor=_dim_value(name),
                          zone=_dim_value(zone))
        samples.append(_sample("hw.temperature", value, dims, "Cel"))
    return samples


def samples_from_leak_detection_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Map LeakDetector rows into per-detector leak-state gauges.

    :param rows: LeakDetector rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: per-detector ``hw.leak.state`` samples derived from the rows.
    """
    samples = []
    for row in rows:
        value = _leak_state_value(row.get("DetectorState"))
        if value is None:
            continue
        chassis = str(row.get("Chassis") or "unknown")
        detector = str(row.get("Id") or row.get("Name") or row.get("Uri") or "detector")
        dims = _with_dims(
            identity,
            source="leak-detector",
            chassis=chassis,
            detector=_dim_value(detector),
            detector_state=_dim_value(row.get("DetectorState")),
        )
        if row.get("LeakDetectorType"):
            dims["detector_type"] = _dim_value(row["LeakDetectorType"])
        if row.get("Health"):
            dims["health"] = _dim_value(row["Health"])
        if row.get("State"):
            dims["state"] = _dim_value(row["State"])
        samples.append(_sample("hw.leak.state", value, dims, None))
    return samples


def samples_from_network_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Expose NIC/DPU inventory health as lightweight fabric presence gauges.

    :param rows: network-adapter inventory rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: ``hw.fabric.adapter_present`` presence samples for each adapter.
    """
    samples = []
    for row in rows:
        adapter = str(row.get("Id") or "adapter")
        dims = _with_dims(identity, source="network-adapter", adapter=_dim_value(adapter))
        dims["device_class"] = str(row.get("DeviceClass") or "NIC")
        if row.get("Model"):
            dims["model"] = _dim_value(row["Model"])
        samples.append(_sample("hw.fabric.adapter_present", 1.0, dims, None))
    return samples


def samples_from_component_integrity_rows(
        rows: Iterable[Mapping],
        identity: Mapping[str, str]) -> list[MetricSample]:
    """Expose ComponentIntegrity enabled state for attested fabric components.

    :param rows: ComponentIntegrity rows to map.
    :param identity: fixed join dimensions applied to every sample.
    :return: ``hw.component_integrity.enabled`` samples for each component.
    """
    samples = []
    for row in rows:
        component = str(row.get("Id") or "component")
        enabled = 1.0 if row.get("Enabled") is True else 0.0
        dims = _with_dims(identity, source="component-integrity", component=_dim_value(component))
        if row.get("Type"):
            dims["component_integrity_type"] = str(row["Type"])
        samples.append(_sample("hw.component_integrity.enabled", enabled, dims, None))
    return samples


def _fan_readings(row: Mapping) -> list[tuple[str, float]]:
    """Extract (name, RPM) pairs from a row's ``FanSpeedsPercent`` list.

    :param row: an EnvironmentMetrics row that may carry fan-speed entries.
    :return: list of (fan name, RPM) tuples with a numeric SpeedRPM.
    """
    readings = []
    for fan in row.get("FanSpeedsPercent") or []:
        if not isinstance(fan, Mapping):
            continue
        rpm = _as_float(fan.get("SpeedRPM"))
        if rpm is None:
            continue
        name = str(fan.get("DeviceName") or fan.get("@odata.id") or "fan").rsplit("/", 1)[-1]
        readings.append((name, rpm))
    return readings


def _leak_state_value(value) -> Optional[float]:
    """Map a leak-detector state string to a gauge value.

    :param value: the ``DetectorState`` string.
    :return: 0.0 for a clear state, 1.0 for a leak, or None when empty.
    """
    if value in (None, ""):
        return None
    state = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    clear_states = {
        "ok",
        "normal",
        "none",
        "absent",
        "notdetected",
        "noleak",
        "noleakdetected",
    }
    return 0.0 if state in clear_states else 1.0


def _environment_chassis(row: Mapping) -> str:
    """Resolve the chassis identifier for an EnvironmentMetrics row.

    :param row: the EnvironmentMetrics row.
    :return: the parent chassis id, falling back to Chassis/Id or ``unknown``.
    """
    parent_type = row.get("ParentType")
    parent_id = row.get("ParentId")
    if parent_type == "Chassis" and parent_id:
        return str(parent_id)
    return str(row.get("Chassis") or row.get("Id") or "unknown")


def _environment_dims(identity: Mapping[str, str],
                      row: Mapping,
                      chassis: str) -> dict[str, str]:
    """Build environment-source dimensions for an EnvironmentMetrics row.

    :param identity: fixed join dimensions to seed the result.
    :param row: the EnvironmentMetrics row supplying parent type/id.
    :param chassis: the resolved chassis identifier.
    :return: dimension mapping including resource/processor/memory context.
    """
    dims = _with_dims(identity, source="environment", chassis=chassis)
    parent_type = row.get("ParentType")
    parent_id = row.get("ParentId")
    if parent_type:
        dims["resource_type"] = str(parent_type)
    if parent_id:
        resource = _dim_value(parent_id)
        dims["resource"] = resource
        if parent_type == "Processor":
            dims["processor"] = resource
        elif parent_type == "Memory":
            dims["memory"] = resource
    return dims


def _environment_gpu(row: Mapping, chassis: str) -> Optional[str]:
    """Resolve the GPU identifier owning an EnvironmentMetrics row, if any.

    :param row: the EnvironmentMetrics row.
    :param chassis: the resolved chassis identifier.
    :return: the ``GPU_<n>`` identifier, or None when the row is not GPU-scoped.
    """
    parent_type = row.get("ParentType")
    parent_id = str(row.get("ParentId") or "")
    if parent_type == "Processor" and parent_id.startswith("GPU_"):
        return parent_id
    if parent_type == "Memory":
        match = re.match(r"(GPU_\d+)", parent_id)
        if match:
            return match.group(1)
    return _gpu_from_chassis(chassis)


def _fabric_dims(identity: Mapping[str, str],
                 system,
                 gpu,
                 port,
                 fabric: str) -> dict[str, str]:
    """Build fabric-source dimensions for a link/port sample.

    :param identity: fixed join dimensions to seed the result.
    :param system: system identifier, if known.
    :param gpu: GPU identifier, if known.
    :param port: port identifier, if known.
    :param fabric: fabric type label (e.g. ``nvlink`` or ``ib``).
    :return: dimension mapping for the fabric sample.
    """
    dims = _with_dims(identity, source="fabric", fabric=fabric)
    for key, value in (("system", system), ("gpu", gpu), ("port", port)):
        if value:
            dims[key] = str(value)
    return dims


def _gpu_from_chassis(chassis: str) -> Optional[str]:
    """Extract the GPU identifier embedded in a chassis name.

    :param chassis: the chassis identifier.
    :return: the ``GPU_<n>`` identifier, or None when none is present.
    """
    parts = chassis.split("HGX_")
    if len(parts) == 2 and parts[1].startswith("GPU_"):
        return parts[1]
    return chassis if chassis.startswith("GPU_") else None


def _gpu_from_metric_info(info: Mapping[str, str]) -> Optional[str]:
    """Resolve a GPU identifier from parsed MetricProperty fields.

    :param info: parsed MetricProperty fields (gpu, memory, chassis, sensor, …).
    :return: the ``GPU_<n>`` identifier, or None when none can be resolved.
    """
    gpu = str(info.get("gpu") or "")
    if gpu.startswith("GPU_"):
        return gpu
    memory = str(info.get("memory") or "")
    match = re.match(r"(GPU_\d+)", memory)
    if match:
        return match.group(1)
    chassis_gpu = _gpu_from_chassis(str(info.get("chassis") or ""))
    if chassis_gpu:
        return chassis_gpu
    sensor = str(info.get("sensor") or info.get("property") or "")
    match = re.search(r"(?:^|_)(GPU_\d+)(?:_|$)", sensor)
    return match.group(1) if match else None


def _gpu_dim(chassis: str) -> dict[str, str]:
    """Build a ``gpu`` dimension dict from a chassis name.

    :param chassis: the chassis identifier.
    :return: ``{"gpu": <id>}`` when a GPU is present, else an empty dict.
    """
    gpu = _gpu_from_chassis(chassis)
    return {"gpu": gpu} if gpu else {}


def _parse_metric_property(prop: str) -> dict[str, str]:
    """Parse a Redfish MetricProperty URI into its addressing fields.

    :param prop: the MetricProperty path (with optional ``#`` fragment).
    :return: dict with the property name and any system/gpu/port/chassis/index/source context.
    """
    path, _, fragment = prop.partition("#")
    parts = [part for part in path.strip("/").split("/") if part]
    frag = [p for p in fragment.strip("/").split("/") if p] if fragment else []
    idx = None
    if frag:
        # a trailing numeric segment (e.g. .../NVDECUtilizationPercent/0) is an
        # array index, not the metric name — keep the name, expose the index
        if frag[-1].isdigit() and len(frag) >= 2:
            prop_name, idx = frag[-2], frag[-1]
        else:
            prop_name = frag[-1]
    else:
        prop_name = parts[-1] if parts else "metric"
    info = {"property": prop_name}
    if idx is not None:
        info["index"] = idx
    if "Sensors" in parts:
        info["metric_source"] = "sensor"
    elif "MemoryMetrics" in parts or "Memory" in parts or "MemorySummary" in parts:
        info["metric_source"] = "memory"
    elif "ProcessorMetrics" in parts:
        info["metric_source"] = "processor"
    for collection, key in (("Systems", "system"), ("Processors", "gpu"),
                            ("Memory", "memory"), ("Ports", "port"),
                            ("Chassis", "chassis"), ("Sensors", "sensor")):
        if collection in parts:
            i = parts.index(collection) + 1
            if i < len(parts):
                info[key] = parts[i]
    return info


def _generic_metric_name(prop: str) -> str:
    """Vendor-neutral metric name for any MetricReport property not in the
    curated fabric map, so the full telemetry surface is exported rather than
    just fabric counters. e.g. ``FP16ActivityPercent`` -> ``hw.gb300.fp16_activity_percent``.

    :param prop: the MetricReport property name.
    :return: the vendor-neutral ``hw.gb300.*`` metric name.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", prop)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    return f"hw.gb300.{snake or 'metric'}"


def _is_gpu_temperature(prop: str) -> bool:
    """Whether a property name denotes a GPU temperature reading.

    :param prop: the property name to test.
    :return: True if the name refers to a temperature.
    """
    lowered = prop.lower()
    return "temp" in lowered or "temperature" in lowered


class SupermicroExporterReader(AbstractExporterReader):
    """Supermicro/GB300 telemetry reader.

    Implements the :class:`AbstractExporterReader` contract: pure data
    adaptation from this vendor's collected Redfish rows into the shared
    :class:`MetricSample` model. It performs no Redfish transport — the command
    collects the raw rows and hands them here.
    """

    def build_metric_samples(
            self,
            identity: Mapping[str, str],
            environment_rows: Iterable[Mapping] = (),
            sensor_rows: Iterable[Mapping] = (),
            nvlink_rows: Iterable[Mapping] = (),
            metric_report_rows: Iterable[Mapping] = (),
            thermal_rows: Iterable[Mapping] = (),
            leak_detection_rows: Iterable[Mapping] = (),
            network_rows: Iterable[Mapping] = (),
            component_integrity_rows: Iterable[Mapping] = ()) -> list[MetricSample]:
        """Adapt Supermicro/GB300 collected rows into shared samples.

        :param identity: fixed join dimensions applied to every sample.
        :param environment_rows: Chassis EnvironmentMetrics rows (power/energy/fan).
        :param sensor_rows: Redfish Sensor rows (thermal/fan/voltage/power).
        :param nvlink_rows: nvlink-ports rows for per-link fabric metrics.
        :param metric_report_rows: TelemetryService MetricReport rows.
        :param thermal_rows: ThermalSubsystem temperature rows.
        :param leak_detection_rows: LeakDetector rows.
        :param network_rows: NIC/DPU network-adapter inventory rows.
        :param component_integrity_rows: ComponentIntegrity rows.
        :return: combined vendor-neutral MetricSample list from all row sources.
        """
        return build_metric_samples(
            identity,
            environment_rows,
            sensor_rows,
            nvlink_rows,
            metric_report_rows,
            thermal_rows,
            leak_detection_rows,
            network_rows,
            component_integrity_rows,
        )
