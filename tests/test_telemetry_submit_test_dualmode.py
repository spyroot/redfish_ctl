"""Dual-mode-style coverage for TelemetryService.SubmitTestMetricReport."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_XR8620T_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_XR8620T_INDEX = {
    path.name.lower(): path for path in DELL_XR8620T_CORPUS.glob("*.json")
}
TELEMETRY_SERVICE = "/redfish/v1/TelemetryService"
SUBMIT_TARGET = (
    "/redfish/v1/TelemetryService/Actions/"
    "TelemetryService.SubmitTestMetricReport"
)


def _fixture_for_path(path):
    """Return the extracted Dell XR8620t fixture matching a Redfish path."""
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_XR8620T_INDEX.get(name.lower())


@pytest.fixture
def dell_xr8620t_telemetry_manager():
    """Serve the committed Dell XR8620t corpus over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/telemetry-test-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/telemetry-test-1"},
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-xr8620t",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def _submit(manager, **kwargs):
    """Invoke the telemetry test metric command with concise defaults."""
    return manager.sync_invoke(
        ApiRequestType.TelemetrySubmitTest,
        "telemetry-submit-test",
        metric_report_name="SyntheticReport",
        metric_id="SyntheticMetric",
        metric_value="42",
        **kwargs,
    )


def test_telemetry_submit_test_without_confirm_is_preview_only(
        dell_xr8620t_telemetry_manager):
    """SubmitTestMetricReport resolves the target but does not POST by default."""
    manager, requests = dell_xr8620t_telemetry_manager

    result = _submit(manager)

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#TelemetryService.SubmitTestMetricReport"
    assert result.data["target"] == SUBMIT_TARGET
    assert result.data["payload"] == {
        "MetricReportName": "SyntheticReport",
        "GeneratedMetricReportValues": [
            {
                "MetricId": "SyntheticMetric",
                "MetricValue": "42",
            },
        ],
    }
    assert result.data["level"] == "reversible"
    assert result.data["blocked"] == "test metric report submission requires --confirm"
    assert _post_requests(requests) == []


def test_telemetry_submit_test_confirm_posts_payload(
        dell_xr8620t_telemetry_manager):
    """--confirm POSTs the generated metric report to the discovered action."""
    manager, requests = dell_xr8620t_telemetry_manager

    result = _submit(manager, confirm=True)

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#TelemetryService.SubmitTestMetricReport"
    assert result.data["target"] == SUBMIT_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == "telemetry-test-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == SUBMIT_TARGET.lower()
    assert posts[0].json() == {
        "MetricReportName": "SyntheticReport",
        "GeneratedMetricReportValues": [
            {
                "MetricId": "SyntheticMetric",
                "MetricValue": "42",
            },
        ],
    }


def test_telemetry_submit_test_confirm_dry_run_still_does_not_post(
        dell_xr8620t_telemetry_manager):
    """--dry_run remains a no-POST preview even when --confirm is also set."""
    manager, requests = dell_xr8620t_telemetry_manager

    result = _submit(manager, confirm=True, dry_run=True)

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == SUBMIT_TARGET
    assert result.data["payload"]["MetricReportName"] == "SyntheticReport"
    assert _post_requests(requests) == []


def test_telemetry_submit_test_metric_property_is_optional(
        dell_xr8620t_telemetry_manager):
    """MetricProperty is included only when supplied by the caller."""
    manager, requests = dell_xr8620t_telemetry_manager

    result = _submit(
        manager,
        metric_property="/redfish/v1/TelemetryService#/ServiceEnabled",
        dry_run=True,
    )

    assert result.error is None
    [metric] = result.data["payload"]["GeneratedMetricReportValues"]
    assert metric == {
        "MetricId": "SyntheticMetric",
        "MetricValue": "42",
        "MetricProperty": "/redfish/v1/TelemetryService#/ServiceEnabled",
    }
    assert _post_requests(requests) == []


def test_telemetry_submit_test_no_action_reports_clear_error(redfish_mock_factory):
    """A corpus without SubmitTestMetricReport fails clearly before any POST."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.TelemetrySubmitTest,
        "telemetry-submit-test",
        confirm=True,
    )

    assert result.error == (
        "action '#TelemetryService.SubmitTestMetricReport' "
        f"not found on {TELEMETRY_SERVICE}"
    )
    assert result.data["action"] == "#TelemetryService.SubmitTestMetricReport"
    assert _post_requests(service.requests) == []
