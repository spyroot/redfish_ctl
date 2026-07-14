"""Dual-mode tests for EventDestination subscription lifecycle commands."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

SUBSCRIPTIONS_PATH = "/redfish/v1/EventService/Subscriptions"
SUBSCRIPTION_ONE_PATH = f"{SUBSCRIPTIONS_PATH}/1"
DESTINATION = "https://listener.example.com/redfish/events"


def _request_type(name):
    request_type = getattr(ApiRequestType, name, None)
    assert request_type is not None, f"missing ApiRequestType.{name}"
    return request_type


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def _seed_subscription(service):
    service._overlay[SUBSCRIPTIONS_PATH.lower()] = {
        "@odata.id": SUBSCRIPTIONS_PATH,
        "Members": [{"@odata.id": SUBSCRIPTION_ONE_PATH}],
        "Members@odata.count": 1,
    }
    service._overlay[SUBSCRIPTION_ONE_PATH.lower()] = {
        "@odata.id": SUBSCRIPTION_ONE_PATH,
        "Id": "1",
        "Name": "Test Event Destination",
        "Destination": DESTINATION,
        "Protocol": "Redfish",
    }


def test_subscription_create_dry_run_builds_payload_without_post(
    redfish_mock_factory,
):
    """subscription-create previews the EventDestination payload by default."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type("SubscriptionCreate"),
        "subscription-create",
        destination=DESTINATION,
        event_format_type="Event",
        event_types=["Alert"],
        context="gb300-smoke",
        confirm=False,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "create",
        "target": SUBSCRIPTIONS_PATH,
        "payload": {
            "Destination": DESTINATION,
            "Protocol": "Redfish",
            "EventFormatType": "Event",
            "EventTypes": ["Alert"],
            "Context": "gb300-smoke",
        },
        "note": "preview only; re-run with --confirm to create subscription",
    }
    assert _mutating_requests(service) == []


def test_subscription_create_confirm_posts_event_destination_payload(
    redfish_mock_factory,
):
    """subscription-create --confirm POSTs only the EventDestination body."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type("SubscriptionCreate"),
        "subscription-create",
        destination=DESTINATION,
        registry_prefixes=["Base", "TaskEvent"],
        resource_types=["Task"],
        confirm=True,
    )

    posts = [request for request in service.requests if request.method == "POST"]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "create"
    assert result.data["target"] == SUBSCRIPTIONS_PATH
    assert result.data["status"] == "IdracApiRespond.Success"
    assert len(posts) == 1
    assert posts[0].path.lower() == SUBSCRIPTIONS_PATH.lower()
    assert posts[0].json() == {
        "Destination": DESTINATION,
        "Protocol": "Redfish",
        "RegistryPrefixes": ["Base", "TaskEvent"],
        "ResourceTypes": ["Task"],
    }


def test_subscription_delete_dry_run_resolves_member_without_delete(
    redfish_mock_factory,
):
    """subscription-delete previews the resolved member URI until confirmed."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_subscription(service)

    result = manager.sync_invoke(
        _request_type("SubscriptionDelete"),
        "subscription-delete",
        subscription="1",
        confirm=False,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "delete",
        "target": SUBSCRIPTION_ONE_PATH,
        "note": "preview only; re-run with --confirm to delete subscription",
    }
    assert all(request.method != "DELETE" for request in service.requests)


def test_subscription_delete_confirm_deletes_resolved_member(
    redfish_mock_factory,
):
    """subscription-delete --confirm DELETEs only the resolved member URI."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_subscription(service)

    result = manager.sync_invoke(
        _request_type("SubscriptionDelete"),
        "subscription-delete",
        subscription=SUBSCRIPTION_ONE_PATH,
        confirm=True,
    )

    deletes = [request for request in service.requests if request.method == "DELETE"]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "delete"
    assert result.data["target"] == SUBSCRIPTION_ONE_PATH
    assert result.data["status"] == "IdracApiRespond.Ok"
    assert len(deletes) == 1
    assert deletes[0].path.lower() == SUBSCRIPTION_ONE_PATH.lower()


def test_subscription_commands_fail_closed_without_subscription_collection(
    redfish_mock_factory,
):
    """Subscription writes fail before mutation if EventService has no collection."""
    manager, service = redfish_mock_factory("supermicro")
    event_service = dict(service._state("/redfish/v1/EventService"))
    event_service.pop("Subscriptions", None)
    service._overlay["/redfish/v1/eventservice"] = event_service

    with pytest.raises(InvalidArgument, match="Subscriptions link is not available"):
        manager.sync_invoke(
            _request_type("SubscriptionCreate"),
            "subscription-create",
            destination=DESTINATION,
            confirm=True,
        )
    with pytest.raises(InvalidArgument, match="Subscriptions link is not available"):
        manager.sync_invoke(
            _request_type("SubscriptionDelete"),
            "subscription-delete",
            subscription="1",
            confirm=True,
        )

    assert _mutating_requests(service) == []


def test_subscription_commands_expose_cli_entrypoints():
    """The subscription lifecycle commands are wired into the package registry."""
    registry = IDracManager().get_registry()

    create_type = _request_type("SubscriptionCreate")
    delete_type = _request_type("SubscriptionDelete")
    assert "subscription-create" in registry[create_type]
    assert "subscription-delete" in registry[delete_type]

    create_parser, create_name, create_help = registry[create_type][
        "subscription-create"
    ].register_subcommand(registry[create_type]["subscription-create"])
    delete_parser, delete_name, delete_help = registry[delete_type][
        "subscription-delete"
    ].register_subcommand(registry[delete_type]["subscription-delete"])

    assert create_parser.format_help()
    assert delete_parser.format_help()
    assert create_name == "subscription-create"
    assert delete_name == "subscription-delete"
    assert "subscription" in create_help.lower()
    assert "subscription" in delete_help.lower()
