"""Prometheus exposition writer for telemetry samples.

    redfish_ctl exporter --once --output prometheus
    redfish_ctl exporter --output prometheus            # serve /metrics forever

The Prometheus backend renders shared :class:`MetricSample` objects as ``/metrics``
text and, in serve mode, exposes them over HTTP. It is vendor-neutral: every
vendor reader produces the same samples, so one Prometheus writer serves them all.

Author Mus spyroot@gmail.com
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Iterable

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import identity as identity_mod
from redfish_ctl.telemetry.abstract_exporter_writer import AbstractExporterWriter
from redfish_ctl.telemetry.exporter import metric_definition
from redfish_ctl.telemetry.metric_model import MetricSample


def _prometheus_type(kind: str) -> str:
    """Map exporter metric kind to the Prometheus exposition type.

    :param kind: exporter metric kind.
    :return: Prometheus metric type.
    """
    return "counter" if kind in {"counter", "cumulative_counter"} else "gauge"


def _escape_label_value(value) -> str:
    """Escape a value for a Prometheus label (backslash, newline, quote).

    :param value: the raw label value.
    :return: the escaped label string.
    """
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _escape_help_text(value) -> str:
    """Escape a value for a Prometheus HELP line.

    :param value: raw HELP text.
    :return: escaped HELP text.
    """
    return str(value).replace("\\", "\\\\").replace("\n", "\\n")


def _format_value(value: float) -> str:
    """Format a float as a Prometheus sample value.

    :param value: the numeric sample value.
    :return: an integer string when whole, else the float repr.
    """
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def render_prometheus_text(samples: Iterable[MetricSample]) -> str:
    """Render samples in Prometheus/OpenMetrics text exposition form.

    :param samples: metric samples to render.
    :return: Prometheus/OpenMetrics text exposition of the samples.
    """
    lines = []
    seen_types = set()
    for sample in samples:
        definition = metric_definition(sample.metric)
        prometheus_name = definition.prometheus_name or sample.metric
        if prometheus_name not in seen_types:
            if definition.description:
                lines.append(
                    f"# HELP {prometheus_name} "
                    f"{_escape_help_text(definition.description)}")
            lines.append(f"# TYPE {prometheus_name} {_prometheus_type(definition.kind)}")
            seen_types.add(prometheus_name)
        label_text = ",".join(
            f'{key}="{_escape_label_value(value)}"'
            for key, value in sorted(sample.dimensions.items())
            if key not in identity_mod.RESOURCE_ONLY_DIMENSIONS
        )
        lines.append(f"{prometheus_name}{{{label_text}}} {_format_value(sample.value)}")
    return "\n".join(lines) + "\n"


def serve_prometheus(
        scrape: Callable[[], str],
        bind: str = "0.0.0.0",
        port: int = 9109) -> None:
    """Serve ``/metrics`` by calling ``scrape`` for each request.

    :param scrape: callable returning the Prometheus text body for each request.
    :param bind: address to bind the HTTP server to.
    :param port: TCP port to serve ``/metrics`` on.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            """Serve ``/metrics`` with the scrape body, or 404/500 on error."""
            if self.path != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            try:
                payload = scrape().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:  # noqa: BLE001 - exporter should return HTTP 500
                payload = f"exporter scrape failed: {type(exc).__name__}\n".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002 - http.server API
            """Silence the default per-request stderr logging.

            :param format: log format string (ignored).
            """
            return

    HTTPServer((bind, port), Handler).serve_forever()


class PrometheusWriter(AbstractExporterWriter):
    """Prometheus exposition writer.

    Implements :class:`AbstractExporterWriter`: ``write_once`` renders samples as
    ``/metrics`` text; ``run`` serves ``/metrics`` forever, rendering fresh samples
    per request. It owns its Prometheus listener config (``--listen``/``--port``).
    """

    def __init__(self, listen: str = "0.0.0.0", port: int = 9109):
        """Initialize the writer with its Prometheus listener config.

        :param listen: address to bind the ``/metrics`` HTTP server to.
        :param port: TCP port to serve ``/metrics`` on.
        """
        self._listen = listen
        self._port = port

    def write_once(self, samples: Iterable[MetricSample]) -> CommandResult:
        """Render one scrape of samples as Prometheus text.

        :param samples: shared samples to render.
        :return: CommandResult wrapping the rendered ``/metrics`` text and a
            sample-count summary.
        """
        materialized = list(samples)
        return CommandResult(
            render_prometheus_text(materialized), None,
            {"sample_count": len(materialized)}, None)

    def run(self, scrape_samples: Callable[[], list[MetricSample]]) -> None:
        """Serve ``/metrics`` forever, rendering fresh samples on each request.

        :param scrape_samples: callable returning fresh samples for each scrape.
        """
        serve_prometheus(
            lambda: render_prometheus_text(scrape_samples()),
            self._listen, self._port)
