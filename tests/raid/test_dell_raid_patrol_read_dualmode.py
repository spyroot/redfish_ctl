"""Dual-mode-style coverage for DellRaidService patrol-read actions."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_raid_patrol_read import DellRaidPatrolRead
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
START_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.StartPatrolRead"
STOP_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.StopPatrolRead"


@pytest.fixture
def dell_raid_mock():
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
                idrac_ip="mock-dell-raid",
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


def _overlay_raid_service(service, body):
    """Overlay DellRaidService under both common request casings.

    :param service: the recording MockRedfishService.
    :param body: replacement RAID-service body.
    """
    service._overlay[RAID_SERVICE] = body
    service._overlay[RAID_SERVICE.lower()] = body


def test_dell_raid_patrol_read_lists_targets_without_posting(dell_raid_mock):
    """With no action, the command lists patrol-read targets and never POSTs."""
    manager, service = dell_raid_mock

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
    assert _post_requests(service) == []


def test_dell_raid_patrol_read_previews_start_by_default(dell_raid_mock):
    """A selected patrol-read action previews by default and does not POST."""
    manager, service = dell_raid_mock

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
    assert _post_requests(service) == []


def test_dell_raid_patrol_read_confirm_posts_start(dell_raid_mock):
    """--confirm POSTs StartPatrolRead; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="start",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.StartPatrolRead"
    assert result.data["target"] == START_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == START_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_patrol_read_confirm_posts_stop(dell_raid_mock):
    """--confirm POSTs StopPatrolRead to the corpus-advertised target."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPatrolRead,
        "dell-raid-patrol-read",
        action="stop",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.StopPatrolRead"
    assert result.data["target"] == STOP_TARGET
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == STOP_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_raid_patrol_read_dry_run_overrides_confirm(dell_raid_mock):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_raid_mock

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
    assert _post_requests(service) == []


def test_dell_raid_patrol_read_missing_action_reports_available(
    dell_raid_mock,
):
    """A DellRaidService without StopPatrolRead reports the missing action."""
    manager, service = dell_raid_mock
    body = copy.deepcopy(service._state(RAID_SERVICE))
    body["Actions"].pop("#DellRaidService.StopPatrolRead")
    _overlay_raid_service(service, body)

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
    assert _post_requests(service) == []


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
