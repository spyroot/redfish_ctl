"""Dual-mode-style coverage for TelemetryService.SubmitTestMetricReport."""

from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
TELEMETRY_SERVICE = "/redfish/v1/TelemetryService"
SUBMIT_TARGET = (
    "/redfish/v1/TelemetryService/Actions/"
    "TelemetryService.SubmitTestMetricReport"
)


@pytest.fixture
def dell_telemetry_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    The vendor-faithful service realizes an Action POST the Dell way: 202 plus
    a ``JID_`` OEM job id in the Location header, never a DMTF-generic token.

    :return: tuple of IDracManager and the recording MockRedfishService.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-xr8620t",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: the recording MockRedfishService.
    :return: list of POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


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
        dell_telemetry_mock):
    """SubmitTestMetricReport resolves the target but does not POST by default."""
    manager, service = dell_telemetry_mock

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
    assert _post_requests(service) == []


def test_telemetry_submit_test_confirm_posts_payload(
        dell_telemetry_mock):
    """--confirm POSTs the report; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_telemetry_mock

    result = _submit(manager, confirm=True)

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#TelemetryService.SubmitTestMetricReport"
    assert result.data["target"] == SUBMIT_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
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
        dell_telemetry_mock):
    """--dry_run remains a no-POST preview even when --confirm is also set."""
    manager, service = dell_telemetry_mock

    result = _submit(manager, confirm=True, dry_run=True)

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == SUBMIT_TARGET
    assert result.data["payload"]["MetricReportName"] == "SyntheticReport"
    assert _post_requests(service) == []


def test_telemetry_submit_test_metric_property_is_optional(
        dell_telemetry_mock):
    """MetricProperty is included only when supplied by the caller."""
    manager, service = dell_telemetry_mock

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
    assert _post_requests(service) == []


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
    assert _post_requests(service) == []
