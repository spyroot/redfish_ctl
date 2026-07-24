"""Dual-mode-style coverage for DellRaidService RenameVD."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_rename_vd import DellRaidRenameVD
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
RENAME_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.RenameVD"


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


def test_dell_raid_rename_vd_lists_target_without_posting(
    dell_raid_mock,
):
    """With no payload, the command lists RenameVD and never POSTs."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == [{
        "Action": "rename-vd",
        "FullType": "#DellRaidService.RenameVD",
        "Resource": RAID_SERVICE,
        "Target": RENAME_TARGET,
        "RequiredPayload": ["TargetFQDD", "Name"],
    }]
    assert _post_requests(service) == []


def test_dell_raid_rename_vd_previews_by_default(dell_raid_mock):
    """RenameVD previews by default and does not POST."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
        target_fqdd="Disk.Virtual.0",
        vd_name="data-vd",
    )

    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.RenameVD",
        "target": RENAME_TARGET,
        "payload": {"TargetFQDD": "Disk.Virtual.0", "Name": "data-vd"},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(service) == []


def test_dell_raid_rename_vd_confirm_posts(dell_raid_mock):
    """--confirm POSTs RenameVD; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
        target_fqdd="Disk.Virtual.0",
        vd_name="data-vd",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.RenameVD"
    assert result.data["target"] == RENAME_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == RENAME_TARGET.lower()
    assert posts[0].json() == {"TargetFQDD": "Disk.Virtual.0", "Name": "data-vd"}


def test_dell_raid_rename_vd_dry_run_overrides_confirm(
    dell_raid_mock,
):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
        target_fqdd="Disk.Virtual.0",
        vd_name="data-vd",
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == RENAME_TARGET
    assert _post_requests(service) == []


def test_dell_raid_rename_vd_missing_payload_is_rejected(
    dell_raid_mock,
):
    """The command rejects missing required payload before POST."""
    manager, service = dell_raid_mock

    with pytest.raises(InvalidArgument, match="requires: Name"):
        manager.sync_invoke(
            ApiRequestType.DellRaidRenameVD,
            "dell-raid-rename-vd",
            target_fqdd="Disk.Virtual.0",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_dell_raid_rename_vd_missing_action_reports_available(
    dell_raid_mock,
):
    """A DellRaidService without RenameVD reports the missing action."""
    manager, service = dell_raid_mock
    body = copy.deepcopy(service._state(RAID_SERVICE))
    body["Actions"].pop("#DellRaidService.RenameVD")
    _overlay_raid_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
        target_fqdd="Disk.Virtual.0",
        vd_name="data-vd",
        confirm=True,
    )

    assert result.error == "Dell RAID RenameVD action not found"
    assert result.data["action"] == "#DellRaidService.RenameVD"
    assert "#DellRaidService.SetBootVD" in result.data["available"]
    assert _post_requests(service) == []


def test_dell_raid_rename_vd_policy_and_registry():
    """RenameVD is classified and the command is registered."""
    assert classify("#DellRaidService.RenameVD") is Destructiveness.DESTRUCTIVE

    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidRenameVD][
        "dell-raid-rename-vd"
    ] is DellRaidRenameVD

    cmd_parser, cmd_name, cmd_help = DellRaidRenameVD.register_subcommand(
        DellRaidRenameVD
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-raid-rename-vd"
    assert "virtual disk" in cmd_help
    assert "--target-fqdd" in help_text
    assert "--name" in help_text
