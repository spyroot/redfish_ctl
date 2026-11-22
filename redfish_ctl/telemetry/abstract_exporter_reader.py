"""Abstract contract for a telemetry export reader.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Mapping

from redfish_ctl.telemetry.metric_model import MetricDefinition, MetricSample


class AbstractExporterReader(ABC):
    """Interface a class must satisfy to claim to be a telemetry export reader.

    A concrete reader owns one vendor's complete read side: which Redfish
    resources are collected, how unsupported and failed collectors are reported,
    and how the resulting rows map into the vendor-neutral ``MetricSample`` model.
    The exporter command supplies the selected manager/transport to the reader and
    does not duplicate those vendor decisions.
    """

    @abstractmethod
    def read(self, **kwargs) -> list[MetricSample]:
        """Collect one scrape and return samples ready for a writer."""
        raise NotImplementedError

    @abstractmethod
    def metric_definition(self, metric_name: str) -> MetricDefinition:
        """Resolve a metric definition from this reader's concrete catalog.

        :param metric_name: canonical metric name.
        :return: concrete catalog definition.
        """
        raise NotImplementedError

    @abstractmethod
    def build_metric_samples(self, identity: Mapping[str, str],
                             **rows: Iterable[Mapping]) -> list[MetricSample]:
        """Adapt already-collected rows into shared samples.

        :param identity: fixed join dimensions applied to every sample.
        :param rows: named row sources collected from the BMC; the accepted keys
            are vendor-specific and defined by the concrete reader.
        :return: vendor-neutral samples; the base contract yields none.
        """
        raise NotImplementedError
