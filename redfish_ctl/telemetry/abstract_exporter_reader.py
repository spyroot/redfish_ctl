"""Abstract contract for a telemetry export reader.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:
    from redfish_ctl.telemetry.exporter import MetricSample


class AbstractExporterReader(ABC):
    """Interface a class must satisfy to claim to be a telemetry export reader.

    A reader performs pure data adaptation: it maps a vendor's already-collected
    Redfish rows into the shared, vendor-neutral ``MetricSample`` model. A reader
    never uses the Redfish transport — collecting the raw rows from the BMC is the
    command/Manager's job. Every vendor (Supermicro, DMTF, Dell) provides its own
    reader implementing this contract, so the same metric contract is emitted no
    matter how a given BMC exposes the source data.
    """

    @abstractmethod
    def build_metric_samples(self, identity: Mapping[str, str],
                             **rows: Iterable[Mapping]) -> list[MetricSample]:
        """Adapt collected rows into shared samples.

        :param identity: fixed join dimensions applied to every sample.
        :param rows: named row sources collected from the BMC; the accepted keys
            are vendor-specific and defined by the concrete reader.
        :return: vendor-neutral samples; the base contract yields none.
        """
        return []
