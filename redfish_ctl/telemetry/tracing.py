"""Optional OpenTelemetry tracing for redfish_ctl (off by default).

Emits spans so BMC operations render in an OTLP APM backend (for example Splunk
APM) as a service map + trace waterfall:

* ``sync_invoke`` wraps each command in an operation root span named by the
  command, with status taken from the returned ``CommandResult.error``.
* ``api_get_call`` (and, later, the write verbs) wrap each BMC HTTP call in a
  ``SpanKind.CLIENT`` span. The BMC is uninstrumented, so an APM backend infers
  it as a downstream service from ``peer.service`` — set to the constant
  ``"bmc"`` so a whole fleet collapses into one downstream node, sliced by tag
  rather than exploding into one node per address.

The module is a no-op and does not import the OpenTelemetry SDK until tracing is
explicitly enabled, so the default install and the offline test suite are
unaffected and pay zero cost.

Author Mus <spyroot@gmail.com>
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

# Set by enable_tracing(); None means tracing is off and every helper no-ops.
_TRACER: Any = None

# The single downstream node name for every BMC. Setting peer.service (not just
# server.address) is what makes an APM backend render one inferred "bmc" node
# instead of one node per BMC IP.
BMC_PEER_SERVICE = "bmc"


def enable_tracing(tracer: Any) -> None:
    """Turn tracing on with a ready OpenTelemetry tracer (or off with None)."""
    global _TRACER
    _TRACER = tracer


def setup_otlp(service_name: str = "redfish-ctl") -> None:
    """Install an OTLP span pipeline and enable tracing.

    Builds a ``TracerProvider`` + ``BatchSpanProcessor`` + ``OTLPSpanExporter``
    and turns tracing on. The exporter resolves its endpoint from the standard
    ``OTEL_EXPORTER_OTLP_*`` environment itself, so the ``/v1/traces`` path is
    appended correctly for a generic endpoint (do not pre-pass one here).

    Requires the OpenTelemetry SDK + OTLP exporter (the ``[otlp]`` extra);
    raises a clear error otherwise. ``service.name`` becomes the APM service
    map node.
    """
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - exercised via the CLI path
        raise RuntimeError(
            "OTLP tracing needs the OpenTelemetry SDK. Install redfish_ctl[otlp]."
        ) from exc
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:  # fall back to the HTTP exporter if grpc is absent
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:  # pragma: no cover - exercised via the CLI path
            raise RuntimeError(
                "OTLP tracing needs an OpenTelemetry OTLP exporter. "
                "Install redfish_ctl[otlp]."
            ) from exc

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    enable_tracing(provider.get_tracer("redfish_ctl"))


def disable_tracing() -> None:
    """Turn tracing off (used by tests to restore the default no-op state)."""
    global _TRACER
    _TRACER = None


def is_enabled() -> bool:
    """True when a tracer is installed."""
    return _TRACER is not None


@contextlib.contextmanager
def operation_span(name: str) -> Iterator[Any]:
    """Root/parent span for one command operation. No-op when tracing is off."""
    if _TRACER is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    with _TRACER.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        yield span


@contextlib.contextmanager
def client_span(url: str, method: str) -> Iterator[Any]:
    """CLIENT span for one BMC HTTP call. No-op when tracing is off.

    The BMC becomes an inferred downstream service via ``peer.service``.
    """
    if _TRACER is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    host = urlsplit(url).hostname or ""
    with _TRACER.start_as_current_span(
        "redfish.bmc.request", kind=SpanKind.CLIENT
    ) as span:
        span.set_attribute("peer.service", BMC_PEER_SERVICE)
        if host:
            span.set_attribute("server.address", host)
        span.set_attribute("http.request.method", method)
        yield span


def record_response(span: Any, status_code: Optional[int]) -> None:
    """Attach the HTTP status to a CLIENT span; mark ERROR on a 4xx/5xx."""
    if span is None or status_code is None:
        return
    from opentelemetry.trace import Status, StatusCode

    span.set_attribute("http.response.status_code", int(status_code))
    if int(status_code) >= 400:
        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
        span.set_attribute("error.type", f"http_{status_code}")


def record_exception(span: Any, exc: BaseException) -> None:
    """Mark a span failed from a transport exception (timeout, connect error)."""
    if span is None:
        return
    from opentelemetry.trace import Status, StatusCode

    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    span.set_attribute("error.type", type(exc).__name__)


def record_result(span: Any, result: Any) -> None:
    """Mark an operation span failed when its CommandResult carries an error."""
    if span is None:
        return
    error = getattr(result, "error", None)
    if error:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.set_attribute("error.type", "command_error")
