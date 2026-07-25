"""Abstract contract for a telemetry export writer.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from redfish_ctl.telemetry.exporter import MetricSample


class AbstractExporterWriter(ABC):
    """Interface a class must satisfy to claim to be a telemetry export writer.

    A writer is decoupled from the reader: it consumes the shared ``MetricSample``
    model and owns *where* and *how* samples are emitted (Prometheus text, SignalFx
    push, OTLP), including its own backend config (endpoint, token, settings). The
    reader never knows the destination, so one writer serves every vendor's reader.
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
