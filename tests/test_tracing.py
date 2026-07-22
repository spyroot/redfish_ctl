"""Offline tests for the optional OpenTelemetry tracing scaffold.

A command run through ``sync_invoke`` should, when tracing is enabled, emit an
operation root span named by the command plus a ``SpanKind.CLIENT`` span per BMC
HTTP call. The CLIENT span carries ``peer.service="bmc"`` so an APM backend
renders the BMC as one inferred downstream node. Mutating calls also produce
CLIENT spans and action metadata when routed through the action primitive. With
tracing disabled (the default) commands must behave exactly as before and emit
nothing.

These use an in-memory span exporter — no collector, no network — and skip
cleanly when the OpenTelemetry SDK (the ``[otlp]`` extra) is not installed.

Author Mus <spyroot@gmail.com>
"""
import asyncio
import json

import pytest

from redfish_ctl.firmware.cmd_firmware_update import FirmwareUpdate
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.telemetry import tracing


@pytest.fixture
def span_exporter():
    """Install an in-memory tracer for the duration of one test."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing.enable_tracing(provider.get_tracer("redfish_ctl.test"))
    try:
        yield exporter
    finally:
        tracing.disable_tracing()


def _client_span_attrs(spans, method):
    """Return attributes for BMC client spans matching one HTTP method."""
    return [
        dict(span.attributes)
        for span in spans
        if span.name == "redfish.bmc.request"
        and span.attributes.get("http.request.method") == method
    ]


def test_command_emits_operation_root_and_client_bmc_spans(span_exporter, redfish_mock):
    """One command yields its operation span plus CLIENT spans to the BMC.

    This is the whole feature on the smallest surface: the operation appears in
    an APM service map as ``redfish-ctl -> bmc`` (via peer.service) with a
    waterfall.
    """
    result = redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    assert isinstance(result, CommandResult)

    spans = span_exporter.get_finished_spans()
    names = [s.name for s in spans]

    # Operation root span, named by the command.
    assert "system_query" in names

    # At least one CLIENT span representing a BMC HTTP call.
    client_spans = [s for s in spans if s.name == "redfish.bmc.request"]
    assert client_spans, f"no CLIENT bmc spans among {names}"

    attrs = dict(client_spans[0].attributes)
    # peer.service is the make-or-break attribute: it collapses all BMCs into
    # one inferred downstream node instead of one node per address.
    assert attrs.get("peer.service") == "bmc"
    assert attrs.get("http.request.method") == "GET"
    assert "server.address" in attrs


def test_mutating_verbs_emit_client_bmc_spans(span_exporter, redfish_mock):
    """POST/PATCH/DELETE calls emit CLIENT spans with method and status."""
    redfish_mock.api_post_call(
        "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
        "Actions/ComputerSystem.Reset",
        json.dumps({"ResetType": "On"}),
        {},
    )
    redfish_mock.api_patch_call(
        "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
        json.dumps({"Attributes": {"BootMode": "Uefi"}}),
        {},
    )
    redfish_mock.api_delete_call(
        "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
        "Storage/Volumes/Disk.Virtual.0",
        {},
    )

    spans = span_exporter.get_finished_spans()
    post = _client_span_attrs(spans, "POST")[0]
    patch = _client_span_attrs(spans, "PATCH")[0]
    delete = _client_span_attrs(spans, "DELETE")[0]

    assert post["peer.service"] == "bmc"
    assert post["http.response.status_code"] == 202
    assert patch["http.response.status_code"] == 200
    assert delete["http.response.status_code"] == 200


def test_async_mutating_verbs_emit_client_bmc_spans(span_exporter, redfish_mock):
    """Async POST/PATCH/DELETE wrappers preserve BMC client spans."""
    loop = asyncio.new_event_loop()
    try:
        post_future = loop.run_until_complete(
            redfish_mock.api_async_post_call(
                loop,
                "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
                "Actions/ComputerSystem.Reset",
                json.dumps({"ResetType": "On"}),
                {},
            )
        )
        patch_future = loop.run_until_complete(
            redfish_mock.api_async_patch_call(
                loop,
                "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
                json.dumps({"Attributes": {"BootMode": "Uefi"}}),
                {},
            )
        )
        delete_future = loop.run_until_complete(
            redfish_mock.api_async_delete_call(
                loop,
                "https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
                "Storage/Volumes/Disk.Virtual.0",
                "{}",
                {},
            )
        )

        assert loop.run_until_complete(post_future).status_code == 202
        assert loop.run_until_complete(patch_future).status_code == 200
        assert loop.run_until_complete(delete_future).status_code == 200
    finally:
        loop.close()

    spans = span_exporter.get_finished_spans()
    assert _client_span_attrs(spans, "POST")
    assert _client_span_attrs(spans, "PATCH")
    assert _client_span_attrs(spans, "DELETE")


def test_async_command_emits_operation_root_span(span_exporter, redfish_mock):
    """The async dispatch path opens the command's operation ROOT span too.

    Regression for a live-observed gap: async_invoke dispatched a command without
    the operation_span that sync_invoke has, so an async command's BMC client
    spans surfaced as orphan ``redfish.bmc.request`` root traces in APM instead of
    nesting under one ``<command>`` trace. The root span must be present.
    """
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            redfish_mock.async_invoke(
                ApiRequestType.SystemQuery, "system_query",
                host="mock-idrac", username="root", password="mock", port=443,
                insecure=True, is_http=False))
    finally:
        loop.close()

    assert isinstance(result, CommandResult)
    names = [s.name for s in span_exporter.get_finished_spans()]
    assert "system_query" in names, (
        f"async path missing operation root span; got {names}")


def test_invoke_action_adds_action_metadata_to_post_span(
    span_exporter,
    redfish_mock_factory,
):
    """Action POST spans carry action/type/target/level attributes."""
    mgr, _svc = redfish_mock_factory("supermicro")
    result = mgr.invoke_action(
        "/redfish/v1/EventService",
        "SubmitTestEvent",
        payload={"MessageId": "Alert.1.0.TestEvent"},
        full_action_type="#EventService.SubmitTestEvent",
    )
    assert result.data["executed"] is True

    spans = span_exporter.get_finished_spans()
    post = _client_span_attrs(spans, "POST")[0]
    assert post["redfish.action.name"] == "SubmitTestEvent"
    assert post["redfish.action.type"] == "#EventService.SubmitTestEvent"
    assert post["redfish.action.target"] == (
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    )
    assert post["redfish.action.level"] == "reversible"


def test_firmware_upload_post_emits_action_metadata(
    span_exporter,
    tmp_path,
    monkeypatch,
):
    """Raw firmware upload POSTs are traced even though they bypass base_post."""
    image = tmp_path / "fw.bin"
    image.write_bytes(b"firmware")

    class Response:
        """Minimal response object returned by the monkeypatched upload call."""

        status_code = 202
        headers = {"Location": "/redfish/v1/TaskService/Tasks/JID_1"}

    calls = []

    def fake_post(url, **kwargs):
        """Capture the upload call and return an accepted response."""
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(
        "redfish_ctl.firmware.cmd_firmware_update.requests.post",
        fake_post,
    )

    cmd = FirmwareUpdate(
        idrac_ip="mock-idrac",
        idrac_username="root",
        idrac_password="mock",
    )
    response = cmd._post_image_file(
        "/redfish/v1/UpdateService/upload",
        "HttpPushUri",
        image,
    )

    assert response.status_code == 202
    assert calls and calls[0][0] == "https://mock-idrac/redfish/v1/UpdateService/upload"
    post = _client_span_attrs(span_exporter.get_finished_spans(), "POST")[0]
    assert post["redfish.action.name"] == "FirmwareUpload"
    assert post["redfish.action.type"] == "HttpPushUri"
    assert post["redfish.action.target"] == "/redfish/v1/UpdateService/upload"
    assert post["redfish.action.level"] == "destructive"


def test_client_span_is_child_of_the_operation_span(span_exporter, redfish_mock):
    """CLIENT bmc spans nest under the operation span (the waterfall shape)."""
    redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    spans = span_exporter.get_finished_spans()

    by_id = {s.context.span_id: s for s in spans}
    op = next(s for s in spans if s.name == "system_query")
    client = next(s for s in spans if s.name == "redfish.bmc.request")

    # Walk the parent chain from the client span up to the operation span.
    node = client
    seen = set()
    while node is not None and node.parent is not None and node.context.span_id not in seen:
        seen.add(node.context.span_id)
        node = by_id.get(node.parent.span_id)
        if node is not None and node.context.span_id == op.context.span_id:
            break
    assert node is not None and node.context.span_id == op.context.span_id


def test_disabled_tracing_emits_nothing_and_still_works(redfish_mock):
    """The default (no tracer) path runs the command and records no spans."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Deliberately do NOT enable_tracing — the default no-op state.
    tracing.disable_tracing()

    result = redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    assert isinstance(result, CommandResult)
    assert exporter.get_finished_spans() == ()


def test_helpers_are_noop_without_a_tracer():
    """The span helpers must be safe to call with tracing off."""
    tracing.disable_tracing()
    assert tracing.is_enabled() is False
    with tracing.operation_span("x") as span:
        assert span is None
    with tracing.client_span("https://10.0.0.1/redfish/v1/", "GET") as span:
        assert span is None
    # record_* on a None span must not raise.
    tracing.record_response(None, 500)
    tracing.record_exception(None, RuntimeError("x"))
    tracing.record_result(None, CommandResult(None, None, None, "err"))


def test_setup_otlp_is_idempotent_when_already_configured(monkeypatch):
    """A second controller module import must not rebuild the OTLP pipeline."""
    tracing.enable_tracing(object())
    monkeypatch.setattr(tracing, "_OTLP_SETUP_SERVICE_NAME", "redfish-controller")
    try:
        tracing.setup_otlp("redfish-controller")
    finally:
        tracing.disable_tracing()


def test_trace_resource_attrs_carries_service_name_and_merges_identity():
    """The trace Resource always carries service.name and merges caller identity keys,
    so redfish spans and hw.* metrics correlate on deployment.environment in Splunk."""
    attrs = tracing._trace_resource_attrs(
        "redfish_ctl",
        {"deployment.environment": "nv72-gb300",
         "deployment.environment.name": "nv72-gb300"},
    )
    assert attrs["service.name"] == "redfish_ctl"
    assert attrs["deployment.environment"] == "nv72-gb300"
    assert attrs["deployment.environment.name"] == "nv72-gb300"


def test_trace_resource_attrs_skips_none_and_stringifies():
    """None extras are dropped and values are stringified for the OTLP Resource."""
    attrs = tracing._trace_resource_attrs("redfish_ctl", {"absent": None, "port": 5})
    assert "absent" not in attrs
    assert attrs["port"] == "5"


def test_setup_otlp_defaults_to_shared_redfish_ctl_service_name(monkeypatch):
    """setup_otlp() with no argument resolves service.name to the shared redfish_ctl
    identity (unified with metrics) — the old 'redfish-ctl' split-brain default is gone.

    Proven offline via the idempotency early-return: pre-seeding the guard with
    redfish_ctl makes a no-arg setup_otlp() return before any SDK import. If the default
    were still 'redfish-ctl' the names would differ, the guard would miss, and it would
    try to build the pipeline and raise without the [otlp] extra installed.
    """
    from redfish_ctl.telemetry.identity import DEFAULT_SERVICE_NAME

    assert DEFAULT_SERVICE_NAME == "redfish_ctl"
    tracing.enable_tracing(object())
    monkeypatch.setattr(tracing, "_OTLP_SETUP_SERVICE_NAME", DEFAULT_SERVICE_NAME)
    try:
        tracing.setup_otlp()
    finally:
        tracing.disable_tracing()


@pytest.mark.parametrize("path, expected", [
    ("/redfish/v1/Systems/System.Embedded.1", "Systems"),
    ("/redfish/v1/Chassis", "Chassis"),
    ("/redfish/v1/Managers/iDRAC.Embedded.1/EthernetInterfaces", "Managers"),
    ("/redfish/v1/UpdateService/FirmwareInventory", "UpdateService"),
    ("/redfish/v1/TelemetryService/MetricReports/Sensor", "TelemetryService"),
    ("/redfish/v1", "ServiceRoot"),
    ("/redfish/v1/", "ServiceRoot"),
    ("", "ServiceRoot"),
    ("/api/custom/thing", "api"),
    ("/redfish/v1/systems/System.Embedded.1", "Systems"),   # lower-case canonicalized
    ("/redfish/v1/CHASSIS", "Chassis"),                     # upper-case canonicalized
    ("/redfish/v1/Systems/", "Systems"),                    # trailing slash on collection
    ("/redfish/v2/Managers", "Managers"),                   # future version segment stripped
    ("/redfish/v1/Oem/Contoso/Widget", "Oem"),              # unknown/OEM segment preserved
])
def test_path_family_groups_by_top_level_collection(path, expected):
    """_path_family collapses a request path to its low-cardinality Redfish family.

    Instance ids (``System.Embedded.1``) and deeper segments must not raise the
    cardinality: every path under one collection shares one family label, the
    service root maps to ``ServiceRoot``, and a non-Redfish path falls back to
    its first segment.
    """
    assert tracing._path_family(path) == expected


def test_client_span_sets_required_path_family(span_exporter):
    """Every BMC client span carries the contract-required redfish.path_family.

    ``redfish.path_family`` is a required attribute in span_contract.yaml, so a
    request span that omitted it would fail the contract gate. This asserts the
    attribute is emitted and derived from the request path.
    """
    with tracing.client_span(
            "https://bmc.example/redfish/v1/Systems/System.Embedded.1", "GET"):
        pass
    request_spans = [
        s for s in span_exporter.get_finished_spans()
        if s.name == "redfish.bmc.request"
    ]
    assert request_spans, "expected one redfish.bmc.request span"
    for span in request_spans:
        assert span.attributes.get("redfish.path_family") == "Systems"


def test_client_span_fixed_attributes_are_not_overridable(span_exporter):
    """Caller attributes never clobber the internally-derived contract keys.

    A caller that passes a same-named attribute (path_family, method,
    server.address, peer.service) must not override the value client_span
    derives; server.address is present even when the URL has no host; and a
    non-fixed attribute still passes through.
    """
    with tracing.client_span(
            "https:///redfish/v1/Chassis",  # deliberately hostless
            "GET",
            attributes={
                "redfish.path_family": "SPOOFED",
                "http.request.method": "DELETE",
                "server.address": "spoofed",
                "peer.service": "notbmc",
                "redfish.vendor": "Dell",
            }):
        pass
    attrs = next(
        dict(s.attributes) for s in span_exporter.get_finished_spans()
        if s.name == "redfish.bmc.request"
    )
    assert attrs["redfish.path_family"] == "Chassis"
    assert attrs["http.request.method"] == "GET"
    assert attrs["peer.service"] == "bmc"
    assert attrs["server.address"] == "unknown"   # hostless URL still sets it
    assert attrs["redfish.vendor"] == "Dell"      # non-fixed attr passes through


def test_span_contract_declares_every_emitted_request_attribute():
    """span_contract.yaml lists every attribute the code emits on request spans.

    Guards the contract-vs-code reconciliation: the four ``redfish.action.*``
    keys (emitted by the action primitive) and ``redfish.path_family`` (emitted
    by every ``client_span``) must be declared, or the contract gate's
    ``unknown attribute keys == 0`` / ``missing required keys == 0`` checks break.
    """
    import pathlib

    import yaml

    contract_path = pathlib.Path(__file__).resolve().parent.parent / \
        "specs" / "telemetry" / "span_contract.yaml"
    request_span = yaml.safe_load(contract_path.read_text())["request_span"]
    required = set(request_span.get("required", []))
    allowed = required | set(request_span.get("optional", []))

    action_keys = {
        "redfish.action.name", "redfish.action.type",
        "redfish.action.target", "redfish.action.level",
    }
    missing = (action_keys | {"redfish.path_family"}) - allowed
    assert not missing, f"span_contract omits emitted request attributes: {missing}"
    assert "redfish.path_family" in required
    assert "redfish.action" not in allowed, \
        "singular redfish.action is stale; code emits redfish.action.* keys"
