"""Dual-mode tests for EventDestination subscription lifecycle commands."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
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
    assert result.data["status"] == "RedfishApiRespond.Success"
    assert len(posts) == 1
    assert posts[0].path.lower() == SUBSCRIPTIONS_PATH.lower()
    assert posts[0].json() == {
        "Destination": DESTINATION,
        "Protocol": "Redfish",
        "RegistryPrefixes": ["Base", "TaskEvent"],
        "ResourceTypes": ["Task"],
    }


def test_subscription_create_splits_comma_separated_filters(
    redfish_mock_factory,
):
    """subscription-create accepts repeated or comma-separated filter values."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type("SubscriptionCreate"),
        "subscription-create",
        destination=DESTINATION,
        event_types=["Alert,StatusChange", "ResourceUpdated"],
        registry_prefixes="Base, TaskEvent",
        resource_types=["Task, MetricReport"],
        confirm=False,
    )

    assert result.error is None
    assert _mutating_requests(service) == []
    assert result.data["payload"]["EventTypes"] == [
        "Alert",
        "StatusChange",
        "ResourceUpdated",
    ]
    assert result.data["payload"]["RegistryPrefixes"] == ["Base", "TaskEvent"]
    assert result.data["payload"]["ResourceTypes"] == ["Task", "MetricReport"]


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
    assert result.data["status"] == "RedfishApiRespond.Ok"
    assert len(deletes) == 1
    assert deletes[0].path.lower() == SUBSCRIPTION_ONE_PATH.lower()


def test_subscription_delete_rejects_collection_uri_without_delete(
    redfish_mock_factory,
):
    """subscription-delete never accepts the collection URI as a delete target."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_subscription(service)

    with pytest.raises(InvalidArgument, match="subscription member URI"):
        manager.sync_invoke(
            _request_type("SubscriptionDelete"),
            "subscription-delete",
            subscription=SUBSCRIPTIONS_PATH,
            confirm=True,
        )

    assert all(request.method != "DELETE" for request in service.requests)


def test_subscription_delete_rejects_other_collection_uri_without_delete(
    redfish_mock_factory,
):
    """subscription-delete rejects URIs outside the EventDestination collection."""
    manager, service = redfish_mock_factory("supermicro")
    _seed_subscription(service)

    with pytest.raises(InvalidArgument, match="must be under"):
        manager.sync_invoke(
            _request_type("SubscriptionDelete"),
            "subscription-delete",
            subscription="/redfish/v1/EventService/Other/1",
            confirm=True,
        )

    assert all(request.method != "DELETE" for request in service.requests)


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
    registry = RedfishManagerBase().get_registry()

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
    assert "collection URI" not in delete_parser.format_help()


def test_gb300_subscription_roundtrip_script_tracks_created_member(tmp_path):
    """The GB300 live script parses Redfish Members keys during the round-trip."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")

    bin_dir = tmp_path / "bin"
    capture_dir = tmp_path / "captures"
    state_file = tmp_path / "state"
    bin_dir.mkdir()
    state_file.write_text("initial\n")

    fake_redfish_ctl = bin_dir / "redfish_ctl"
    fake_redfish_ctl.write_text(
        textwrap.dedent(
            f"""\
            #!{bash}
            set -euo pipefail

            while [[ "$#" -gt 0 && "$1" == --* ]]; do
              shift
            done

            command="${{1:-}}"
            shift || true
            state="$(cat "{state_file}")"

            case "$command" in
              event-service)
                if [[ "$state" == created ]]; then
                  printf '%s\\n' '{{"data":{{"Subscriptions":{{"Members":[{{"@odata.id":"{SUBSCRIPTION_ONE_PATH}"}},{{"@odata.id":"{SUBSCRIPTIONS_PATH}/new"}}]}}}}}}'
                else
                  printf '%s\\n' '{{"data":{{"Subscriptions":{{"Members":[{{"@odata.id":"{SUBSCRIPTION_ONE_PATH}"}}]}}}}}}'
                fi
                ;;
              subscription-create)
                printf '%s\\n' created > "{state_file}"
                printf '%s\\n' '{{"data":{{"location":"{SUBSCRIPTIONS_PATH}/new"}}}}'
                ;;
              subscription-delete)
                if [[ "$state" == deleted ]]; then
                  printf '%s\\n' '{{"error":"not found"}}'
                  exit 1
                fi
                printf '%s\\n' deleted > "{state_file}"
                printf '%s\\n' '{{"data":{{"status":"deleted"}}}}'
                ;;
              *)
                echo "unexpected command: $command" >&2
                exit 2
                ;;
            esac
            """
        )
    )
    fake_redfish_ctl.chmod(0o755)

    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text(f"#!{bash}\nexit 0\n")
    fake_sleep.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "REDFISH_IP": "192.0.2.10",
        "REDFISH_USERNAME": "root",
        "REDFISH_PASSWORD": "secret",
        "SUBSCRIPTION_DESTINATION": DESTINATION,
        "TRACE_DIR": str(capture_dir),
    }
    script = (
        "scripts/live_sanity_check/supermicro/gb300/"
        "subscription_roundtrip.sh"
    )

    result = subprocess.run(
        [bash, script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS: subscription created and deleted" in result.stdout
