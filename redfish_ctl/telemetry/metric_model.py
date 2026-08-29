"""Shared, vendor-neutral telemetry metric model.

``MetricDefinition`` (a catalog entry) and ``MetricSample`` (one exported
reading) are pure abstractions with no command or vendor ties. Every reader
produces ``MetricSample`` objects and every writer consumes them, so the model
lives at the ``telemetry`` root and is shared by all of redfish_ctl.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

METRIC_KINDS = frozenset({"gauge", "counter", "cumulative_counter"})
METRIC_AVAILABILITY = frozenset({"self", "baseline", "capability", "event"})


@dataclass(frozen=True)
class MetricDefinition:
    """Catalog entry describing one exported telemetry metric."""

    name: str
    kind: str = "gauge"
    unit: Optional[str] = None
    description: str = ""
    prometheus_name: Optional[str] = None
    family: str = "telemetry"
    dimensions: tuple[str, ...] = ()
    liveness: str = "signal"
    availability: str = "baseline"
    profile_required: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize the immutable definition."""
        if self.kind not in METRIC_KINDS:
            raise ValueError(f"unknown metric kind {self.kind!r} for {self.name}")
        if self.availability not in METRIC_AVAILABILITY:
            raise ValueError(
                f"unknown availability {self.availability!r} for {self.name}"
            )
        if not self.prometheus_name:
            object.__setattr__(self, "prometheus_name", self.name)
        object.__setattr__(self, "dimensions", tuple(self.dimensions))


@dataclass(frozen=True)
class MetricSample:
    """One vendor-neutral telemetry sample ready for export."""

    metric: str
    value: float
    dimensions: Mapping[str, str]
    metric_type: str = "gauge"
    unit: Optional[str] = None
    timestamp: Optional[str] = None


def _definition(
        name: str,
        kind: str = "gauge",
        unit: Optional[str] = None,
        description: str = "",
        family: str = "telemetry",
        dimensions: tuple[str, ...] = (),
        liveness: str = "signal",
        availability: str = "baseline",
        profile_required: bool = False) -> MetricDefinition:
    """Construct a catalog definition with concise defaults.

    :param name: exported metric name.
    :param kind: SignalFx/OpenTelemetry metric kind.
    :param unit: optional unit annotation.
    :param description: human-readable metric description.
    :param family: broad metric family used by specs and docs.
    :param dimensions: expected dimension keys for this metric family.
    :param liveness: liveness role, usually ``signal`` or ``scrape``.
    :param availability: inherent availability class from the telemetry catalog.
    :param profile_required: whether deployment profiles must require the metric.
    :return: immutable MetricDefinition.
    """
    return MetricDefinition(
        name=name,
        kind=kind,
        unit=unit,
        description=description,
        family=family,
        dimensions=dimensions,
        liveness=liveness,
        availability=availability,
        profile_required=profile_required,
    )
