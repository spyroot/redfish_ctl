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
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import urlsplit

# Set by enable_tracing(); None means tracing is off and every helper no-ops.
_TRACER: Any = None
_OTLP_SETUP_SERVICE_NAME: str | None = None

# The single downstream node name for every BMC. Setting peer.service (not just
# server.address) is what makes an APM backend render one inferred "bmc" node
# instead of one node per BMC IP.
BMC_PEER_SERVICE = "bmc"
_CLIENT_ATTRIBUTES: ContextVar[dict[str, Any]] = ContextVar(
    "redfish_client_span_attributes", default={}
)


def enable_tracing(tracer: Any) -> None:
    """Turn tracing on with a ready OpenTelemetry tracer (or off with None).

    :param tracer: an OpenTelemetry tracer to install, or None to disable tracing.
    """
    global _TRACER
    _TRACER = tracer


def _trace_resource_attrs(service_name: str,
                          resource_attrs: Optional[Mapping[str, str]] = None) -> dict:
    """Build the OTLP trace Resource attributes for a redfish_ctl run.

    The OpenTelemetry SDK additionally merges ``OTEL_RESOURCE_ATTRIBUTES`` from the
    environment into the Resource, so ``deployment.environment`` and other identity
    keys can be supplied at deploy time (no code change) and still correlate traces
    with the exporter's ``hw.*`` metrics on the shared identity keys.

    :param service_name: the ``service.name`` resource attribute (the APM service-map node).
    :param resource_attrs: optional extra resource attributes to merge; ``None`` values are skipped.
    :return: a resource-attribute dict carrying a non-empty ``service.name``.
    """
    attrs = {"service.name": str(service_name)}
    for key, value in (resource_attrs or {}).items():
        if value is None:
            continue
        attrs[str(key)] = str(value)
    return attrs


def setup_otlp(service_name: Optional[str] = None,
               resource_attrs: Optional[Mapping[str, str]] = None) -> None:
    """Install an OTLP span pipeline and enable tracing.

    Builds a ``TracerProvider`` + ``BatchSpanProcessor`` + ``OTLPSpanExporter``
    and turns tracing on. The exporter resolves its endpoint and headers from the
    standard ``OTEL_EXPORTER_OTLP_*`` environment; point them at the Splunk O11y
    OTLP ingest (``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` = the ingest trace URL,
    ``OTEL_EXPORTER_OTLP_HEADERS`` = ``X-SF-Token=<access-token>``), so no endpoint
    is hard-coded here.

    Requires the OpenTelemetry SDK + OTLP exporter (the ``[otlp]`` extra);
    raises a clear error otherwise.

    :param service_name: value for the ``service.name`` resource attribute (the APM
        service-map node); defaults to the shared ``redfish_ctl`` identity so traces
        and ``hw.*`` metrics land on one service node. An empty value falls back to it.
    :param resource_attrs: optional extra resource attributes (e.g. deployment.environment)
        merged into the trace Resource so traces carry the same identity keys as metrics.
    :raises RuntimeError: when the OpenTelemetry SDK or an OTLP exporter is not installed.
    """
    from .identity import DEFAULT_SERVICE_NAME
    resolved_service_name = str(service_name or "").strip() or DEFAULT_SERVICE_NAME
    global _OTLP_SETUP_SERVICE_NAME
    if _TRACER is not None and _OTLP_SETUP_SERVICE_NAME == resolved_service_name:
        return
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

    provider = TracerProvider(
        resource=Resource.create(
            _trace_resource_attrs(resolved_service_name, resource_attrs)))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    enable_tracing(provider.get_tracer("redfish_ctl"))
    _OTLP_SETUP_SERVICE_NAME = resolved_service_name


def disable_tracing() -> None:
    """Turn tracing off (used by tests to restore the default no-op state)."""
    global _TRACER, _OTLP_SETUP_SERVICE_NAME
    _TRACER = None
    _OTLP_SETUP_SERVICE_NAME = None


def is_enabled() -> bool:
    """True when a tracer is installed.

    :return: True when tracing is on, False when every helper no-ops.
    """
    return _TRACER is not None


@contextlib.contextmanager
def operation_span(name: str) -> Iterator[Any]:
    """Root/parent span for one command operation. No-op when tracing is off.

    :param name: span name, typically the command being executed.
    """
    if _TRACER is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    with _TRACER.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        yield span


@contextlib.contextmanager
def client_span(
    url: str,
    method: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Any]:
    """CLIENT span for one BMC HTTP call. No-op when tracing is off.

    The BMC becomes an inferred downstream service via ``peer.service``.

    :param url: BMC request URL; its hostname becomes ``server.address``.
    :param method: HTTP method for the call (``http.request.method``).
    :param attributes: optional extra attributes to add to the client span.
    """
    if _TRACER is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    host = urlsplit(url).hostname or ""
    span_attributes = dict(_CLIENT_ATTRIBUTES.get())
    if attributes:
        span_attributes.update(attributes)
    with _TRACER.start_as_current_span(
        "redfish.bmc.request", kind=SpanKind.CLIENT
    ) as span:
        span.set_attribute("peer.service", BMC_PEER_SERVICE)
        if host:
            span.set_attribute("server.address", host)
        span.set_attribute("http.request.method", method)
        for key, value in span_attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


@contextlib.contextmanager
def client_span_attributes(attributes: dict[str, Any]) -> Iterator[None]:
    """Temporarily attach extra attributes to nested BMC client spans.

    :param attributes: attributes to merge into each nested ``client_span``.
    :return: context manager yielding None.
    """
    current = dict(_CLIENT_ATTRIBUTES.get())
    current.update(attributes)
    token = _CLIENT_ATTRIBUTES.set(current)
    try:
        yield None
    finally:
        _CLIENT_ATTRIBUTES.reset(token)


def traced_request(
    url: str,
    method: str,
    request_call: Callable[[], Any],
    attributes: Optional[dict[str, Any]] = None,
) -> Any:
    """Run a request callable inside a BMC client span.

    :param url: BMC request URL used for span attributes.
    :param method: HTTP method name.
    :param request_call: zero-argument callable that performs the request.
    :param attributes: optional extra attributes for the span.
    :return: the response returned by ``request_call``.
    """
    with client_span(url, method, attributes=attributes) as span:
        try:
            response = request_call()
        except Exception as exc:
            record_exception(span, exc)
            raise
        record_response(span, getattr(response, "status_code", None))
        return response


def traced_request_callable(
    url: str,
    method: str,
    request_call: Callable[[], Any],
    attributes: Optional[dict[str, Any]] = None,
) -> Callable[[], Any]:
    """Wrap a request callable for executor use while preserving trace context.

    :param url: BMC request URL used for span attributes.
    :param method: HTTP method name.
    :param request_call: zero-argument callable that performs the request.
    :param attributes: optional extra attributes for the span.
    :return: callable suitable for ``run_in_executor``.
    """
    span_attributes = dict(_CLIENT_ATTRIBUTES.get())
    if attributes:
        span_attributes.update(attributes)
    if _TRACER is None:
        return request_call

    from opentelemetry import context

    parent_context = context.get_current()

    def _wrapped() -> Any:
        """Run the request with the parent trace context attached.

        :return: the response returned by ``request_call``.
        """
        token = context.attach(parent_context)
        try:
            return traced_request(
                url,
                method,
                request_call,
                attributes=span_attributes,
            )
        finally:
            context.detach(token)

    return _wrapped


def record_response(span: Any, status_code: Optional[int]) -> None:
    """Attach the HTTP status to a CLIENT span; mark ERROR on a 4xx/5xx.

    :param span: the CLIENT span to annotate, or None to no-op.
    :param status_code: HTTP response status code, or None to no-op.
    """
    if span is None or status_code is None:
        return
    from opentelemetry.trace import Status, StatusCode

    span.set_attribute("http.response.status_code", int(status_code))
    if int(status_code) >= 400:
        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
        span.set_attribute("error.type", f"http_{status_code}")


def record_exception(span: Any, exc: BaseException) -> None:
    """Mark a span failed from a transport exception (timeout, connect error).

    :param span: the span to annotate, or None to no-op.
    :param exc: the transport exception to record on the span.
    """
    if span is None:
        return
    from opentelemetry.trace import Status, StatusCode

    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    span.set_attribute("error.type", type(exc).__name__)


def record_result(span: Any, result: Any) -> None:
    """Mark an operation span failed when its CommandResult carries an error.

    :param span: the operation span to annotate, or None to no-op.
    :param result: a CommandResult whose ``error`` attribute, when set, marks the span failed.
    """
    if span is None:
        return
    error = getattr(result, "error", None)
    if error:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.set_attribute("error.type", "command_error")
