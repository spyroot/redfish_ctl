"""OTLP telemetry writer package.

Re-exports the emit surface (unchanged import path ``redfish_ctl.telemetry.otlp``)
plus the :class:`AbstractExporterWriter` adapter.
"""

from .emit import (
    RESOURCE_DIM_KEYS,
    is_monotonic_counter,
    metrics_data_from_samples,
    push_otlp,
    resolve_otlp_config,
    run_otlp_loop,
)
from .writer import OtlpWriter

__all__ = [
    "OtlpWriter",
    "RESOURCE_DIM_KEYS",
    "is_monotonic_counter",
    "metrics_data_from_samples",
    "push_otlp",
    "resolve_otlp_config",
    "run_otlp_loop",
]
