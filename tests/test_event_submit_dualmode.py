"""Dual-mode tests for the event-submit-test command."""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service."""
    return [
        request
        for request in redfish_service.requests
        if request.method == "POST"
    ]


def test_event_submit_test_posts_payload_in_mock_mode(redfish_mock, redfish_service):
    """event_submit_test POSTs the reversible test-event payload by default."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.EventSubmitTest,
        "event_submit_test",
        message_id="Alert.1.0.TestEvent",
        event_type="Alert",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "reversible"
    assert result.data["target"] == (
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    )

    post_requests = _post_requests(redfish_service)
    assert len(post_requests) == 1
    request = post_requests[0]
    assert request.path.lower() == (
        "/redfish/v1/eventservice/actions/eventservice.submittestevent"
    )
    assert request.json() == {
        "MessageId": "Alert.1.0.TestEvent",
        "EventType": "Alert",
    }


def test_event_submit_test_dry_run_suppresses_post_in_mock_mode(
    redfish_mock, redfish_service
):
    """event_submit_test dry_run resolves the action target without a POST."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.EventSubmitTest,
        "event_submit_test",
        message_id="Alert.1.0.TestEvent",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "reversible"
    assert result.data["target"] == (
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    )
    assert result.data["payload"] == {"MessageId": "Alert.1.0.TestEvent"}
    assert _post_requests(redfish_service) == []
