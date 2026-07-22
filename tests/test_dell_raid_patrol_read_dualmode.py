"""Dual-mode-style coverage for DellRaidService patrol-read actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.raid.cmd_raid_patrol_read import DellRaidPatrolRead
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
START_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.StartPatrolRead"
STOP_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.StopPatrolRead"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _corpus_body(path):
    """Return one Dell corpus fixture body as JSON.

    :param path: Redfish resource path to read from the extracted corpus.
    :return: parsed fixture payload.
    """
    fixture = _fixture_for_path(path)
    if fixture is None:
        raise AssertionError(f"missing Dell fixture for {path}")
    return json.loads(fixture.read_text())


@pytest.fixture
def dell_raid_manager_factory():
    """Serve the committed Dell corpus over requests-mock.

    :return: factory producing a manager and recorded requests list.
    """
    requests_mock = pytest.importorskip("requests_mock")
    started = []

    def factory(service_body=None):
        requests = []

        def get_cb(request, context):
            requests.append(request)
            if request.path.lower() == RAID_SERVICE.lower() and service_body is not None:
                context.status_code = 200
                return json.dumps(service_body)
            fixture = _fixture_for_path(request.path)
            if fixture is None:
                context.status_code = 404
                return json.dumps({"error": f"no fixture for {request.path}"})
            context.status_code = 200
            return fixture.read_text()

        def post_cb(request, context):
            requests.append(request)
            context.status_code = 202
            context.headers["Location"] = "/redfish/v1/TaskService/Tasks/raid-patrol-1"
            return json.dumps({
                "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/raid-patrol-1"}
            })

        mocker = requests_mock.Mocker()
        mocker.start()
        started.append(mocker)
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-raid",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        return manager, requests

    yield factory

    for mocker in reversed(started):
        mocker.stop()


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_raid_patrol_read_lists_targets_without_posting(dell_raid_manager_factory):
    """With no action, the command lists patrol-read targets and never POSTs."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["service"] == RAID_SERVICE
    assert result.data["actions"] == {
        "start": START_TARGET,
        "stop": STOP_TARGET,
    }
    assert "#DellRaidService.StartPatrolRead" in result.data["available"]
    assert "#DellRaidService.StopPatrolRead" in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_raid_patrol_read_previews_start_by_default(dell_raid_manager_factory):
    """A selected patrol-read action previews by default and does not POST."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="start",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.StartPatrolRead",
        "target": START_TARGET,
        "payload": {},
        "level": "reversible",
        "blocked": None,
    }
    assert _post_requests(requests) == []


def test_dell_raid_patrol_read_confirm_posts_start(dell_raid_manager_factory):
    """--confirm POSTs StartPatrolRead to the corpus-advertised target."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="start",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.StartPatrolRead"
    assert result.data["target"] == START_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == "raid-patrol-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == START_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_patrol_read_confirm_posts_stop(dell_raid_manager_factory):
    """--confirm POSTs StopPatrolRead to the corpus-advertised target."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="stop",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.StopPatrolRead"
    assert result.data["target"] == STOP_TARGET
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == STOP_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_patrol_read_dry_run_overrides_confirm(dell_raid_manager_factory):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="stop",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == STOP_TARGET
    assert _post_requests(requests) == []


def test_dell_raid_patrol_read_missing_action_reports_available(
    dell_raid_manager_factory,
):
    """A DellRaidService without StopPatrolRead reports the missing action."""
    service_body = _corpus_body(RAID_SERVICE)
    service_body["Actions"] = dict(service_body["Actions"])
    service_body["Actions"].pop("#DellRaidService.StopPatrolRead")
    manager, requests = dell_raid_manager_factory(service_body=service_body)

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="stop",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#DellRaidService.StopPatrolRead"
    assert "#DellRaidService.StartPatrolRead" in result.data["available"]
    assert "#DellRaidService.StopPatrolRead" not in result.data["available"]
    assert result.error == (
        "action '#DellRaidService.StopPatrolRead' not found on "
        + RAID_SERVICE
    )
    assert _post_requests(requests) == []


def test_dell_raid_patrol_read_policy_and_registry():
    """Patrol-read actions are classified and the command is registered."""
    assert classify("#DellRaidService.StartPatrolRead") is Destructiveness.REVERSIBLE
    assert classify("#DellRaidService.StopPatrolRead") is Destructiveness.REVERSIBLE

    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidPatrolRead]["dell-raid-patrol-read"] is (
        DellRaidPatrolRead
    )

    cmd_parser, cmd_name, cmd_help = DellRaidPatrolRead.register_subcommand(
        DellRaidPatrolRead
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-raid-patrol-read"
    assert "patrol" in cmd_help
    assert "--action" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
