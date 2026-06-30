"""Dual-mode-style mock tests for vendor-neutral action commands."""
from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_event_submit_test_posts_payload_to_discovered_target_in_mock_mode(
    redfish_mock_factory,
):
    """event-submit-test POSTs the requested event to the discovered action target."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.EventSubmitTest,
        "event_submit_test",
        message_id="Alert.1.0.TestEvent",
        event_type="Alert",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent"
    )
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/eventservice/actions/eventservice.submittestevent"
    )
    assert posts[0].json() == {
        "MessageId": "Alert.1.0.TestEvent",
        "EventType": "Alert",
    }


def test_action_list_returns_supermicro_action_inventory_without_posts(
    redfish_mock_factory,
):
    """actions inventories linked Redfish action targets without mutating state."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(ApiRequestType.ActionList, "action_list")

    full_types = {row["FullType"] for row in result.data}
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data
    assert "#ComputerSystem.Reset" in full_types
    assert "#EventService.SubmitTestEvent" in full_types
    assert all(row["Target"] for row in result.data)
    assert all(row["Level"] for row in result.data)
    assert _post_requests(service) == []


def test_system_reset_confirm_posts_reset_payload_to_host_action_in_mock_mode(
    redfish_mock_factory,
):
    """system-reset --confirm POSTs one reset payload to the discovered host target."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.SystemReset,
        "system_reset",
        reset_type="ForceRestart",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset"
    )
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/systems/system_0/actions/computersystem.reset"
    )
    assert posts[0].json() == {"ResetType": "ForceRestart"}
