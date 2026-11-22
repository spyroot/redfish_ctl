"""Supermicro/NV72 exporter metric definitions.

The DMTF telemetry model defines how a BMC exposes metric reports; it does not
define the ``hw.*`` names emitted by this exporter.  Those names and the
``hw.gb300.*`` catch-all are therefore owned by the concrete Supermicro reader,
not by the shared exporter runtime or its vendor-neutral writers.
"""

from __future__ import annotations

from typing import Mapping, Optional

from redfish_ctl.telemetry import identity as identity_mod
from redfish_ctl.telemetry.exporter import metric_definition as shared_metric_definition
from redfish_ctl.telemetry.metric_model import MetricDefinition, _definition

_COMMON_DIMS = identity_mod.IDENTITY_DIMENSIONS + ("source",)
_FABRIC_DIMS = _COMMON_DIMS + ("fabric", "system", "gpu", "port", "report")

_COUNTER_SUFFIXES = (
    "_bytes", "_frames", "_packets", "_errors", "_discards", "_count", "_wait",
)
_COUNTER_EXACT = frozenset({"hw.energy_kwh"})

_METRIC_DEFINITIONS = (
    _definition("hw.power", unit="W", description="Power draw in watts."),
    _definition("hw.energy_kwh", "counter", "kWh",
                "Cumulative energy consumed in kilowatt-hours."),
    _definition("hw.temperature", unit="Cel", description="Temperature in Celsius."),
    _definition("hw.voltage", unit="V", description="Voltage reading."),
    _definition("hw.fan_speed", unit="RPM",
                description="Fan speed in revolutions per minute."),
    _definition("hw.gpu.power", unit="W", description="GPU power draw in watts.",
                family="gpu"),
    _definition("hw.gpu.temperature", unit="Cel", description="GPU temperature.",
                family="gpu"),
    _definition("hw.gpu.clock_mhz", unit="MHz", description="GPU operating clock.",
                family="gpu"),
    _definition("hw.gpu.compute.utilization", unit="%",
                description="GPU compute engine utilization.", family="gpu"),
    _definition("hw.gpu.throttle.duration_seconds", "counter", "s",
                description="GPU throttle duration in seconds.", family="gpu"),
    _definition("hw.gpu.memory.bandwidth_utilization", unit="%",
                description="GPU memory bandwidth utilization.", family="gpu"),
    _definition("hw.gpu.memory.capacity_utilization", unit="%",
                description="GPU memory capacity utilization.", family="gpu"),
    _definition("hw.gpu.memory.clock_mhz", unit="MHz",
                description="GPU memory operating speed.", family="gpu"),
    _definition("hw.gpu.memory.ecc_errors", "counter", None,
                "Cumulative GPU memory ECC error count.", family="gpu"),
    _definition("hw.gpu.memory.row_remap_count", "counter", None,
                "Cumulative GPU memory row-remap count.", family="gpu"),
    _definition("hw.gpu.memory.row_remapping_failed", unit=None,
                description="GPU memory row-remapping failure state.", family="gpu"),
    _definition("hw.component.health", description="One-hot component health state.",
                family="state"),
    _definition("hw.component.health_rollup",
                description="One-hot component aggregate health state.", family="state"),
    _definition("hw.component.state", description="One-hot component enabled state.",
                family="state"),
    _definition("hw.component.last_reset_type",
                description="One-hot component last reset type.", family="state"),
    _definition("hw.power.edp_violation_state",
                description="One-hot EDP violation state.", family="state"),
    _definition("hw.power.break_performance_state",
                description="One-hot power-break performance state.", family="state"),
    _definition("hw.fabric.link_up", description="Fabric link-up state.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_down_reason",
                description="One-hot fabric link-down reason.", family="fabric",
                dimensions=_FABRIC_DIMS + ("reason",)),
    _definition("hw.fabric.port_speed", unit="Gbps",
                description="Fabric port negotiated speed.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.bit_error_rate", description="Fabric bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.effective_ber", description="Fabric effective bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_ber", description="Fabric raw bit error rate.",
                family="fabric", dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_gbps", unit="Gbps",
                description="Fabric receive bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_gbps", unit="Gbps",
                description="Fabric transmit bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_rx_gbps", unit="Gbps",
                description="Fabric raw receive bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_tx_gbps", unit="Gbps",
                description="Fabric raw transmit bandwidth.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_bytes", "counter", "By",
                "Cumulative fabric receive bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_bytes", "counter", "By",
                "Cumulative fabric transmit bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_tx_bytes", "counter", "By",
                "Cumulative fabric VL15 transmit bytes.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_frames", "counter", None,
                "Cumulative fabric receive frames.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_frames", "counter", None,
                "Cumulative fabric transmit frames.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_tx_packets", "counter", None,
                "Cumulative fabric VL15 transmit packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.crc_errors", "counter", None,
                "Cumulative fabric CRC errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.effective_errors", "counter", None,
                "Cumulative fabric effective errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.fec_errors", "counter", None,
                "Cumulative fabric FEC errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.malformed_packets", "counter", None,
                "Cumulative malformed fabric packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.raw_errors", "counter", None,
                "Cumulative fabric raw errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_errors", "counter", None,
                "Cumulative fabric receive errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_no_protocol_bytes", "counter", "By",
                "Cumulative receive bytes without protocol.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_remote_physical_errors", "counter", None,
                "Cumulative receive remote physical errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.rx_switch_relay_errors", "counter", None,
                "Cumulative receive switch relay errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.symbol_errors", "counter", None,
                "Cumulative fabric symbol errors.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_discards", "counter", None,
                "Cumulative fabric transmit discards.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_no_protocol_bytes", "counter", "By",
                "Cumulative transmit bytes without protocol.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.tx_wait", "counter", None,
                "Cumulative fabric transmit wait events.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.intentional_link_down_count", "counter", None,
                "Cumulative intentional link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_down_count", "counter", None,
                "Cumulative link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.link_error_recovery_count", "counter", None,
                "Cumulative link error recovery count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.unintentional_link_down_count", "counter", None,
                "Cumulative unintentional link-down count.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.fabric.vl15_dropped", "counter", None,
                "Cumulative fabric VL15 dropped packets.", family="fabric",
                dimensions=_FABRIC_DIMS),
    _definition("hw.leak.state", description="Leak detector state.", family="chassis"),
    _definition("hw.fabric.adapter_present", description="Network adapter presence.",
                family="fabric"),
    _definition("hw.component_integrity.enabled",
                description="ComponentIntegrity enabled state.", family="component"),
)

METRIC_DEFINITIONS = {
    definition.name: definition for definition in _METRIC_DEFINITIONS
}


def metric_definitions() -> Mapping[str, MetricDefinition]:
    """Return the Supermicro/NV72-owned metric catalog.

    :return: mapping from metric name to concrete definition.
    """
    return METRIC_DEFINITIONS


def metric_definition(metric_name: str) -> MetricDefinition:
    """Return one shared or Supermicro/NV72 metric definition.

    :param metric_name: canonical metric name.
    :return: concrete, dynamic GB300, or shared self-metric definition.
    """
    if metric_name in METRIC_DEFINITIONS:
        return METRIC_DEFINITIONS[metric_name]
    if metric_name.startswith("hw.gb300."):
        return MetricDefinition(
            name=metric_name,
            kind=_infer_metric_kind(metric_name),
            unit=_infer_metric_unit(metric_name),
            description="GB300 MetricReport numeric property.",
            family="gb300",
            dimensions=_COMMON_DIMS + (
                "property", "system", "gpu", "port", "chassis", "index", "report",
            ),
        )
    return shared_metric_definition(metric_name)


def _infer_metric_kind(metric_name: str) -> str:
    """Infer a kind for the dynamic ``hw.gb300.*`` metric family.

    :param metric_name: dynamic GB300 metric name.
    :return: ``counter`` for cumulative metrics, otherwise ``gauge``.
    """
    if metric_name in _COUNTER_EXACT:
        return "counter"
    if any(metric_name.endswith(suffix) for suffix in _COUNTER_SUFFIXES):
        return "counter"
    return "gauge"


def _infer_metric_unit(metric_name: str) -> Optional[str]:
    """Infer a unit for the dynamic ``hw.gb300.*`` metric family.

    :param metric_name: dynamic GB300 metric name.
    :return: canonical unit symbol, or ``None`` when no unit is implied.
    """
    lowered = metric_name.lower()
    if lowered.endswith("_bytes"):
        return "By"
    if lowered.endswith("_gbps") or lowered.endswith("port_speed"):
        return "Gbps"
    if lowered.endswith("_mhz") or "_freq_" in lowered or "frequency" in lowered:
        return "MHz"
    if lowered.endswith("_seconds"):
        return "s"
    if lowered.endswith("_kwh") or "energy" in lowered:
        return "kWh"
    if "temp" in lowered or "temperature" in lowered:
        return "Cel"
    if "power" in lowered:
        return "W"
    if "voltage" in lowered:
        return "V"
    if "percent" in lowered or "utilization" in lowered:
        return "%"
    return None
