"""Abstract contract for a telemetry export writer.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable

from redfish_ctl.telemetry.metric_model import MetricSample


class AbstractExporterWriter(ABC):
    """Interface a class must satisfy to claim to be a telemetry export writer.

    A writer is decoupled from the reader: it consumes the shared ``MetricSample``
    model and owns *where* and *how* samples are emitted (Prometheus text, SignalFx
    push, OTLP), including its own backend config (endpoint, token, settings). The
    reader never knows the destination, so writers stay independent of the
    concrete reader selected by a vendor command.
    """

    @abstractmethod
    def write_once(self, samples: Iterable[MetricSample]) -> object:
        """Emit one scrape of samples and return a backend-specific summary.

        :param samples: shared samples produced by a reader.
        :return: a backend-specific result (rendered text, push status, canary
            summary, …).
        """

    @abstractmethod
    def run(self, scrape_samples: Callable[[], list[MetricSample]]) -> None:
        """Serve or push forever, calling ``scrape_samples`` each cycle.

        :param scrape_samples: callable returning fresh samples for each cycle.
        """
