"""Dual-mode-style mock tests for vendor-neutral action commands."""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _mutating_requests(service):
    """Return non-GET requests recorded by the mock Redfish service."""
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def test_action_list_discovers_dell_actions_in_dual_mode(redfish_api):
    """actions lists Dell reset targets from the dual-mode client."""
    result = redfish_api.sync_invoke(ApiRequestType.ActionList, "action_list")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert isinstance(result.data, list)
    assert result.data
    json.dumps(result.data)

    required_keys = {"Resource", "Action", "FullType", "Target", "Level"}
    assert all(required_keys <= row.keys() for row in result.data)
    targets_by_type = {(row["FullType"], row["Target"]) for row in result.data}
    expected_targets = {
        (
            "#ComputerSystem.Reset",
            "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
        ),
        (
            "#Manager.Reset",
            "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Manager.Reset",
        ),
        (
            "#Chassis.Reset",
            "/redfish/v1/Chassis/System.Embedded.1/Actions/Chassis.Reset",
        ),
        (
            "#Bios.ResetBios",
            "/redfish/v1/Systems/System.Embedded.1/Bios/Settings/Actions/Bios.ResetBios",
        ),
    }
    assert expected_targets <= targets_by_type

    levels_by_type = {row["FullType"]: row["Level"] for row in result.data}
    for full_type, _target in expected_targets:
        assert levels_by_type[full_type] == "destructive"


def test_action_list_records_no_mutating_requests_in_mock_mode(
    redfish_mock,
    redfish_service,
):
    """actions uses only GET requests when inventorying the mock tree."""
    result = redfish_mock.sync_invoke(ApiRequestType.ActionList, "action_list")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data
    assert _mutating_requests(redfish_service) == []


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


def test_action_list_does_not_post_in_mock_mode(redfish_mock, redfish_service):
    """action_list is read-only and sends no POST requests in mock mode."""
    result = redfish_mock.sync_invoke(ApiRequestType.ActionList, "action_list")

    assert isinstance(result, CommandResult)
    assert result.data
    assert not [request for request in redfish_service.requests if request.method == "POST"]


def test_action_list_returns_dell_action_inventory(redfish_api):
    """action_list inventories Dell action rows from linked resources."""
    result = redfish_api.sync_invoke(ApiRequestType.ActionList, "action_list")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.data

    full_types = {row["FullType"] for row in result.data}
    assert {
        "#ComputerSystem.Reset",
        "#Manager.Reset",
        "#Chassis.Reset",
        "#Bios.ResetBios",
    }.issubset(full_types)
    assert all(row["Target"] for row in result.data)
    assert all(row["Level"] for row in result.data)
