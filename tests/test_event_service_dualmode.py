"""Dual-mode tests for the event-service read command."""
import json

from redfish_ctl.events.cmd_event_service import EventServiceQuery
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _mutation_methods(redfish_service):
    """Return mutating requests recorded by the mock Redfish service."""
    return {
        request.method
        for request in redfish_service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    }


def test_event_service_reads_gb300_sse_and_subscription_summary(
    redfish_mock_factory,
):
    """event-service reports SSE support and follows the subscriptions link."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.EventServiceQuery,
        "event-service",
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data == {
        "Id": "EventService",
        "Name": "Event Service",
        "ServiceEnabled": True,
        "Health": None,
        "State": "Enabled",
        "ServerSentEventUri": "/redfish/v1/EventService/SSE",
        "SSEFilterPropertiesSupported": {
            "EventFormatType": True,
            "MessageId": True,
            "MetricReportDefinition": True,
            "OriginResource": False,
            "RegistryPrefix": True,
            "ResourceType": False,
        },
        "EventFormatTypes": ["Event", "MetricReport"],
        "EventTypesForSubscription": [],
        "RegistryPrefixes": [
            "Base",
            "BiosAttributeRegistry",
            "OpenBMC",
            "Platform",
            "ResourceEvent",
            "SensorEvent",
            "TaskEvent",
            "Telemetry",
            "Update",
        ],
        "ResourceTypes": [
            "Task",
            "AccountService",
            "ManagerAccount",
            "SessionService",
            "EventService",
            "UpdateService",
            "Chassis",
            "Systems",
            "Managers",
            "CertificateService",
            "VirtualMedia",
        ],
        "Subscriptions": {
            "uri": "/redfish/v1/EventService/Subscriptions",
            "count": 0,
            "members": [],
        },
    }
    assert _mutation_methods(service) == set()


def test_event_service_uses_event_types_when_event_formats_are_absent(
    redfish_mock_factory,
):
    """event-service keeps older EventService payloads useful without SSE fields."""
    manager, _service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.EventServiceQuery,
        "event-service",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["ServiceEnabled"] is True
    assert result.data["ServerSentEventUri"] is None
    assert result.data["SSEFilterPropertiesSupported"] == {}
    assert result.data["EventFormatTypes"] == []
    assert result.data["EventTypesForSubscription"] == [
        "StatusChange",
        "ResourceUpdated",
        "ResourceAdded",
        "ResourceRemoved",
        "Alert",
    ]
    assert result.data["Subscriptions"] == {
        "uri": "/redfish/v1/EventService/Subscriptions",
        "count": None,
        "members": [],
    }


def test_event_service_tolerates_missing_subscriptions_link(redfish_mock_factory):
    """event-service returns a stable shape when Subscriptions is not advertised."""
    manager, service = redfish_mock_factory("generic")
    event_service = dict(service._state("/redfish/v1/EventService"))
    event_service.pop("Subscriptions", None)
    service._overlay["/redfish/v1/EventService"] = event_service
    service._overlay["/redfish/v1/eventservice"] = event_service

    result = manager.sync_invoke(
        ApiRequestType.EventServiceQuery,
        "event-service",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["Subscriptions"] == {
        "uri": None,
        "count": None,
        "members": [],
    }


def test_event_service_exposes_cli_entrypoint():
    """The event-service command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.EventServiceQuery]["event-service"] is EventServiceQuery

    cmd_parser, cmd_name, cmd_help = EventServiceQuery.register_subcommand(
        EventServiceQuery
    )

    assert cmd_parser.format_help()
    assert cmd_name == "event-service"
    assert "EventService" in cmd_help
