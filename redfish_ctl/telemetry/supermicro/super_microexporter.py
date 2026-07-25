"""Concrete Supermicro/GB300 reader for the Redfish exporter.

This module maps Supermicro/NV72 Redfish
row shapes (Chassis EnvironmentMetrics, Sensors, nvlink-ports, TelemetryService
MetricReports, ThermalSubsystem, LeakDetectors, NetworkAdapters,
ComponentIntegrity) into the shared, vendor-neutral :class:`MetricSample` model
defined in :mod:`redfish_ctl.telemetry.metric_model`.

Generic dimension/coercion primitives are reused from the shared exporter
runtime.  The ``hw.*``/``hw.gb300.*`` catalog and sample construction stay in
this concrete package because those names describe the Supermicro/NV72 corpus,
not the DMTF telemetry schema.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Iterable, Mapping, Optional

from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import (
    CommandResult,
    RedfishManager,
    RedfishResponseCache,
)
from redfish_ctl.telemetry import exporter
from redfish_ctl.telemetry import identity as identity_mod
from redfish_ctl.telemetry.abstract_exporter_reader import AbstractExporterReader
from redfish_ctl.telemetry.exporter import (
    MetricSample,
    _as_float,
    _dim_value,
    _duration_seconds,
    _reading,
    _sample as _catalog_sample,
    _with_dims,
)
from redfish_ctl.telemetry.supermicro.metric_catalog import (
    metric_definition as supermicro_metric_definition,
    metric_definitions as supermicro_metric_definitions,
)


def _sample(*args, **kwargs) -> MetricSample:
    """Build a sample using the concrete Supermicro/NV72 catalog.

    :return: catalog-validated metric sample.
    """
    kwargs["definition_lookup"] = supermicro_metric_definition
    return _catalog_sample(*args, **kwargs)


def _unit_for_metric(metric: str) -> Optional[str]:
    """Return the canonical unit from the Supermicro/NV72 catalog.

    :param metric: canonical metric name.
    :return: catalog unit, or ``None`` for unitless metrics.
    """
    return supermicro_metric_definition(metric).unit

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
            samples.append(_sample(
                "hw.fan_speed",
                rpm,
                dims | {"fan": _dim_value(fan_name)},
                "RPM",
            ))
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
            samples.append(_sample(
                metric,
                value,
                dims | {label: _dim_value(name)},
                row.get("ReadingUnits"),
            ))
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
        samples.append(_sample(
            metric,
            value,
            dims,
            _unit_for_metric(metric),
            row.get("Timestamp"),
        ))
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
    """Supermicro/NV72 metric name for an uncurated MetricReport property.

    This keeps the full concrete telemetry surface instead of dropping values
    outside the curated fabric map. For example, ``FP16ActivityPercent`` becomes
    ``hw.gb300.fp16_activity_percent``.

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
    """Concrete Supermicro/NV72 telemetry reader."""

    _UNSUPPORTED_COLLECTOR_ERRORS = {
        "FailedDiscoverAction",
        "MissingResource",
        "RedfishMethodNotAllowed",
        "RedfishNotFound",
        "ResourceNotFound",
        "UnsupportedAction",
    }
    _AUTHENTICATION_ERRORS = {
        "AuthenticationFailed",
        "RedfishForbidden",
        "RedfishUnauthorized",
    }

    def __init__(self, source: RedfishManager):
        """Bind the reader to the already-selected Supermicro manager.

        :param source: selected manager used for all collector dispatch.
        """
        self._source = source
        self._collector_error_totals: dict[tuple[str, str], float] = {}
        self._last_success_timestamp_seconds = 0.0

    def metric_definition(self, metric_name: str):
        """Resolve one concrete metric or a shared exporter self-metric.

        :param metric_name: canonical metric name.
        :return: concrete or shared metric definition.
        """
        try:
            return supermicro_metric_definition(metric_name)
        except KeyError:
            return exporter.metric_definition(metric_name)

    @staticmethod
    def metric_definitions():
        """Return the concrete catalog plus shared exporter self-metrics.

        :return: merged metric-definition mapping.
        """
        return exporter.metric_definitions() | supermicro_metric_definitions()

    @classmethod
    def _is_unsupported_collector_error(cls, exc: Exception) -> bool:
        """Return whether a collector exception means the resource is unsupported.

        :param exc: collector exception.
        :return: whether the exception represents an unavailable resource.
        """
        return exc.__class__.__name__ in cls._UNSUPPORTED_COLLECTOR_ERRORS

    @classmethod
    def _collector_error_kind(cls, exc: Exception) -> str:
        """Map a collector exception to a bounded exporter error label.

        :param exc: collector exception.
        :return: stable low-cardinality error label.
        """
        name = exc.__class__.__name__
        lowered = name.lower()
        if isinstance(exc, TimeoutError) or "timeout" in lowered:
            return "timeout"
        if name in cls._AUTHENTICATION_ERRORS or "unauthoriz" in lowered:
            return "authentication"
        if "jsondecode" in lowered or "decode" in lowered:
            return "decode_error"
        if name in {"TypeError", "ValueError", "KeyError", "UnexpectedResponse"}:
            return "invalid_payload"
        if "http" in lowered or "redfish" in lowered or "requestfailed" in lowered:
            return "http_error"
        return "internal"

    @staticmethod
    def _validate_collector_rows(rows) -> tuple[Mapping, ...]:
        """Materialize and validate a collector's row sequence.

        :param rows: collector row iterable.
        :return: validated tuple of mapping rows.
        """
        if isinstance(rows, (str, bytes, dict)) or not hasattr(rows, "__iter__"):
            raise ValueError("collector returned a non-list payload")
        normalized = tuple(rows)
        if not all(isinstance(row, Mapping) for row in normalized):
            raise ValueError("collector returned a non-mapping row")
        return normalized

    @staticmethod
    def _extract_list_rows(data) -> list:
        """Extract a collector result whose data is already a row list.

        :param data: collector command data.
        :return: validated list-shaped data.
        """
        if not isinstance(data, list):
            raise ValueError("collector returned a non-list payload")
        return data

    @staticmethod
    def _extract_environment_rows(data) -> list:
        """Extract EnvironmentMetrics rows from its command result.

        :param data: environment-metrics command data.
        :return: environment metric rows.
        """
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            return data["metrics"]
        if isinstance(data, list):
            return data
        raise ValueError("environment-metrics returned an unexpected payload")

    @staticmethod
    def _extract_leak_detector_rows(data) -> list:
        """Extract LeakDetector rows from its command result.

        :param data: leak-detectors command data.
        :return: leak detector rows.
        """
        if isinstance(data, dict) and isinstance(data.get("detectors"), list):
            return data["detectors"]
        raise ValueError("leak-detectors returned an unexpected payload")

    @staticmethod
    def _extract_thermal_rows(data) -> list:
        """Extract ThermalSubsystem rows from its command result.

        :param data: thermal command data.
        :return: temperature-reading rows.
        """
        if isinstance(data, dict) and isinstance(data.get("temperature_readings"), list):
            return data["temperature_readings"]
        raise ValueError("thermal returned an unexpected payload")

    def _collect_result(
            self,
            collector: str,
            call: Callable[[], CommandResult],
            extract_rows: Callable[[object], list],
            ) -> exporter.CollectorResult:
        """Run one collector without losing unsupported and failed outcomes.

        :param collector: stable collector name.
        :param call: deferred collector invocation.
        :param extract_rows: command-specific row extractor.
        :return: normalized collector outcome with duration and error state.
        """
        started_at = exporter.time.monotonic()
        try:
            result = call()
        except Exception as exc:
            duration = exporter.time.monotonic() - started_at
            if self._is_unsupported_collector_error(exc):
                return exporter.CollectorResult(
                    collector, False, True, duration, (), None)
            return exporter.CollectorResult(
                collector, True, False, duration, (),
                self._collector_error_kind(exc))
        duration = exporter.time.monotonic() - started_at
        if not hasattr(result, "data"):
            return exporter.CollectorResult(
                collector, True, False, duration, (), "invalid_payload")
        if getattr(result, "error", None):
            return exporter.CollectorResult(
                collector, True, False, duration, (), "internal")
        try:
            rows = self._validate_collector_rows(extract_rows(result.data))
        except Exception as exc:
            return exporter.CollectorResult(
                collector, True, False, duration, (),
                self._collector_error_kind(exc))
        return exporter.CollectorResult(collector, True, True, duration, rows, None)

    def _invoke_collector(
            self,
            api_type: ApiRequestType,
            name: str,
            extract_rows: Callable[[object], list],
            redfish_cache: Optional[RedfishResponseCache] = None,
            **kwargs,
            ) -> exporter.CollectorResult:
        """Invoke one registered read-only collector through the selected manager.

        :param api_type: registered collector request type.
        :param name: registered collector command name.
        :param extract_rows: command-specific row extractor.
        :param redfish_cache: scrape-scoped response cache.
        :return: normalized collector outcome.
        """
        return self._collect_result(
            name,
            lambda: self._source.sync_invoke(
                api_type,
                name,
                redfish_cache=redfish_cache,
                **kwargs,
            ),
            extract_rows,
        )

    @staticmethod
    def _vendor_label(vendor: Optional[str]) -> str:
        """Return the fixed label for this concrete reader and reject cross-wiring.

        :param vendor: optional requested vendor label.
        :return: fixed ``supermicro`` label.
        """
        if vendor and str(vendor).lower() != "supermicro":
            raise ValueError(
                "the Supermicro exporter reader cannot emit another vendor label")
        return "supermicro"

    def read(
            self,
            label_bmc_ip: Optional[str] = None,
            vendor: Optional[str] = None,
            do_async: bool = False,
            do_expanded: bool = False,
            identity_host_prefix: Optional[str] = None,
            identity_bmc_octet_base: Optional[int] = None,
            identity_server_octet_base: Optional[int] = None,
            identity_server_subnet: Optional[str] = None,
            deployment_environment: Optional[str] = None,
            deployment_environment_compat: Optional[str] = None,
            require_deployment_environment: Optional[bool] = None,
            extra_dimensions: Optional[Mapping | list[str]] = None,
            service_name: Optional[str] = None,
            service_namespace: Optional[str] = None,
            service_instance_id: Optional[str] = None,
            service_version: Optional[str] = None,
            service_criticality: Optional[str] = None,
            otlp_traces: bool = False,
            ) -> list[MetricSample]:
        """Collect one Supermicro/NV72 scrape and return writer-ready samples.

        :param label_bmc_ip: explicit BMC identity label.
        :param vendor: requested vendor label; must be Supermicro when supplied.
        :param do_async: issue collector reads through asynchronous paths.
        :param do_expanded: request expanded resources where supported.
        :param identity_host_prefix: optional normalized host prefix.
        :param identity_bmc_octet_base: first BMC octet used for identity mapping.
        :param identity_server_octet_base: first server octet used for mapping.
        :param identity_server_subnet: subnet used to derive server identity.
        :param deployment_environment: canonical deployment environment label.
        :param deployment_environment_compat: compatibility environment label.
        :param require_deployment_environment: require an environment identity.
        :param extra_dimensions: additional fixed metric dimensions.
        :param service_name: telemetry service name.
        :param service_namespace: telemetry service namespace.
        :param service_instance_id: stable telemetry service instance UUID.
        :param service_version: telemetry service version.
        :param service_criticality: service criticality dimension.
        :param otlp_traces: enable trace emission while scraping.
        :return: complete scrape samples, including exporter health metrics.
        """
        started_at = exporter.time.monotonic()
        redfish_cache = RedfishResponseCache()
        identity_options = identity_mod.resolve_identity_options(
            host_prefix=identity_host_prefix,
            bmc_octet_base=identity_bmc_octet_base,
            server_octet_base=identity_server_octet_base,
            server_subnet=identity_server_subnet,
            deployment_environment=deployment_environment,
            deployment_environment_compat=deployment_environment_compat,
            require_deployment_environment=require_deployment_environment,
            extra_dimensions=extra_dimensions,
            service_name=service_name,
            service_namespace=service_namespace,
            service_instance_id=service_instance_id,
            service_version=service_version,
            service_criticality=service_criticality,
        )
        if identity_options["service_instance_id"] is None:
            identity_options["service_instance_id"] = (
                self._source._default_service_instance_id(
                    redfish_cache,
                    do_async=do_async,
                )
            )
        telemetry_identity = identity_mod.build_legacy_gb300_identity(
            label_bmc_ip or self._source.redfish_ip,
            vendor=self._vendor_label(vendor),
            **identity_options,
        )
        if otlp_traces:
            from redfish_ctl.telemetry import tracing
            tracing.setup_otlp(
                telemetry_identity.service_name,
                telemetry_identity.resource_attributes(),
            )
        identity = telemetry_identity.dimensions()
        collector_results = [
            self._invoke_collector(
                ApiRequestType.EnvironmentMetrics,
                "environment-metrics",
                self._extract_environment_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.Thermal,
                "thermal",
                self._extract_thermal_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.Sensors,
                "sensors",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.NvLinkPorts,
                "nvlink-ports",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.SupermicroMetricReports,
                "metric-reports",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.LeakDetectors,
                "leak-detectors",
                self._extract_leak_detector_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.NetworkAdapters,
                "network-adapters",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
            self._invoke_collector(
                ApiRequestType.ComponentIntegrity,
                "component-integrity",
                self._extract_list_rows,
                redfish_cache=redfish_cache,
                do_async=do_async,
                do_expanded=do_expanded,
                preserve_errors=True,
            ),
        ]
        rows_by_collector = {
            result.name: result.rows for result in collector_results
        }
        samples = self.build_metric_samples(
            identity=identity,
            environment_rows=rows_by_collector["environment-metrics"],
            sensor_rows=rows_by_collector["sensors"],
            nvlink_rows=rows_by_collector["nvlink-ports"],
            metric_report_rows=rows_by_collector["metric-reports"],
            thermal_rows=rows_by_collector["thermal"],
            leak_detection_rows=rows_by_collector["leak-detectors"],
            network_rows=rows_by_collector["network-adapters"],
            component_integrity_rows=rows_by_collector["component-integrity"],
        )
        scrape_ok, scrape_partial = exporter.collector_scrape_status(collector_results)
        for result in collector_results:
            if not result.error_kind:
                continue
            key = (result.name, result.error_kind)
            self._collector_error_totals[key] = (
                self._collector_error_totals.get(key, 0.0) + 1.0
            )
        if scrape_ok:
            self._last_success_timestamp_seconds = exporter.time.time()
        samples.extend(exporter.scrape_health_samples(
            identity,
            ok=scrape_ok,
            duration_seconds=exporter.time.monotonic() - started_at,
            collector_results=collector_results,
            partial=scrape_partial,
            timestamp_seconds=self._last_success_timestamp_seconds,
            collection_error_totals=self._collector_error_totals,
        ))
        return samples

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
