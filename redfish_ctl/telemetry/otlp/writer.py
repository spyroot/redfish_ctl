"""OTLP (OpenTelemetry) writer for telemetry samples.

    redfish_ctl exporter --once --output otlp
    redfish_ctl exporter --output otlp                  # push on an interval

The OTLP backend maps the shared ``hw.*`` metric contract onto the OpenTelemetry
data model and exports it, so ``redfish_ctl`` drops into an existing OTel
pipeline as one more producer. It is vendor-neutral — every vendor reader emits
the same samples. The emission itself lives in :mod:`.emit`; this file is the
:class:`AbstractExporterWriter` adapter that owns the ``--otlp-*``/``OTEL_*``
config.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry.abstract_exporter_writer import AbstractExporterWriter
from redfish_ctl.telemetry.metric_model import MetricSample

from .emit import push_otlp, run_otlp_loop


class OtlpWriter(AbstractExporterWriter):
    """OTLP writer.

    Implements :class:`AbstractExporterWriter`: ``write_once`` exports one scrape
    of samples via OTLP; ``run`` scrapes and exports on a fixed interval forever.
    It owns its OTLP config (``--otlp-endpoint``/``--otlp-protocol``, resolved
    from ``OTEL_*`` when unset).
    """

    def __init__(self,
                 endpoint: Optional[str] = None,
                 protocol: Optional[str] = None,
                 interval: float = 30.0,
                 service_name: str = "redfish_ctl"):
        """Initialize the writer with its OTLP export config.

        :param endpoint: OTLP collector endpoint; resolved from ``OTEL_*`` when None.
        :param protocol: OTLP transport (``grpc`` or ``http/protobuf``); resolved
            from ``OTEL_*`` when None.
        :param interval: seconds between scrapes in ``run`` mode.
        :param service_name: value for the OTLP ``service.name`` resource attribute.
        """
        self._endpoint = endpoint
        self._protocol = protocol
        self._interval = interval
        self._service_name = service_name

    def write_once(self, samples: Iterable[MetricSample]) -> CommandResult:
        """Export one scrape of samples via OTLP.

        :param samples: shared samples to export.
        :return: CommandResult with a sample-count and export-result summary.
        """
        materialized = list(samples)
        result = push_otlp(
            materialized, service_name=self._service_name,
            endpoint=self._endpoint, protocol=self._protocol)
        return CommandResult(
            None, None,
            {"sample_count": len(materialized), "export_result": str(result)},
            None)

    def run(self, scrape_samples: Callable[[], list[MetricSample]]) -> None:
        """Scrape and export via OTLP on a fixed interval until interrupted.

        :param scrape_samples: callable returning fresh samples for each scrape.
        """
        run_otlp_loop(
            scrape_samples, self._interval, service_name=self._service_name,
            endpoint=self._endpoint, protocol=self._protocol)
