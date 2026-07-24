"""Dual-mode-style coverage for DellRaidService spare actions."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_spare import DellRaidSpareActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
ASSIGN_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.AssignSpare"
UNASSIGN_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.UnassignSpare"
DISK_FQDD = "Disk.Bay.4:Enclosure.Internal.0-1:RAID.Integrated.1-1"
VD_FQDD = "Disk.Virtual.0:RAID.Integrated.1-1"


@pytest.fixture
def dell_raid_spare_mock():
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
                idrac_ip="mock-dell-raid-spare",
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


def _overlay_without_action(service, action_name):
    """Overlay DellRaidService with one action removed, under both casings.

    :param service: the recording MockRedfishService.
    :param action_name: full ``#DellRaidService.*`` action name to remove.
    """
    body = copy.deepcopy(service._state(RAID_SERVICE))
    body["Actions"].pop(action_name, None)
    service._overlay[RAID_SERVICE] = body
    service._overlay[RAID_SERVICE.lower()] = body


def test_dell_raid_spare_lists_targets_and_candidates(dell_raid_spare_mock):
    """Calling dell-raid-spare without an action lists targets without POSTing."""
    manager, service = dell_raid_spare_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidSpareActions,
        "dell-raid-spare",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["raid_service"] == RAID_SERVICE
    assert result.data["actions"]["assign"] == {
        "action": "#DellRaidService.AssignSpare",
        "target": ASSIGN_TARGET,
    }
    assert result.data["actions"]["unassign"] == {
        "action": "#DellRaidService.UnassignSpare",
        "target": UNASSIGN_TARGET,
    }
    assert any(
        drive["id"] == "PCIeSSD.Integrated.1-0"
        for drive in result.data["candidates"]["drives"]
    )
    assert any(
        volume["id"] == "PCIeSSD.Integrated.1-0"
        for volume in result.data["candidates"]["virtual_disks"]
    )
    assert _post_requests(service) == []


def test_dell_raid_spare_assign_defaults_to_preview(dell_raid_spare_mock):
    """AssignSpare is a guarded storage change and does not POST by default."""
    manager, service = dell_raid_spare_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidSpareActions,
        "dell-raid-spare",
        action="assign",
        disk_fqdd=DISK_FQDD,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellRaidService.AssignSpare"
    assert result.data["target"] == ASSIGN_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {"TargetFQDD": DISK_FQDD}
    assert _post_requests(service) == []


def test_dell_raid_spare_assign_dedicated_posts_with_confirm(dell_raid_spare_mock):
    """--confirm POSTs AssignSpare; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_raid_spare_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidSpareActions,
        "dell-raid-spare",
        action="assign",
        disk_fqdd=DISK_FQDD,
        virtual_disk=[VD_FQDD],
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.AssignSpare"
    assert result.data["target"] == ASSIGN_TARGET
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == ASSIGN_TARGET.lower()
    assert posts[0].json() == {
        "TargetFQDD": DISK_FQDD,
        "VirtualDiskArray": [VD_FQDD],
    }


def test_dell_raid_spare_unassign_dry_run_overrides_confirm(dell_raid_spare_mock):
    """--dry_run keeps UnassignSpare as a no-POST preview even with --confirm."""
    manager, service = dell_raid_spare_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidSpareActions,
        "dell-raid-spare",
        action="unassign",
        disk_fqdd=DISK_FQDD,
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["action"] == "#DellRaidService.UnassignSpare"
    assert result.data["target"] == UNASSIGN_TARGET
    assert result.data["payload"] == {"TargetFQDD": DISK_FQDD}
    assert _post_requests(service) == []


def test_dell_raid_spare_unassign_rejects_virtual_disk(dell_raid_spare_mock):
    """UnassignSpare accepts only the physical disk target."""
    manager, service = dell_raid_spare_mock

    with pytest.raises(InvalidArgument, match="only valid with --action assign"):
        manager.sync_invoke(
            ApiRequestType.DellRaidSpareActions,
            "dell-raid-spare",
            action="unassign",
            disk_fqdd=DISK_FQDD,
            virtual_disk=[VD_FQDD],
            confirm=True,
        )
    assert _post_requests(service) == []


def test_dell_raid_spare_reports_missing_action(dell_raid_spare_mock):
    """A service without UnassignSpare returns an actionable no-POST error."""
    manager, service = dell_raid_spare_mock
    _overlay_without_action(service, "#DellRaidService.UnassignSpare")

    result = manager.sync_invoke(
        ApiRequestType.DellRaidSpareActions,
        "dell-raid-spare",
        action="unassign",
        disk_fqdd=DISK_FQDD,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#DellRaidService.UnassignSpare"
    assert "#DellRaidService.AssignSpare" in result.data["available"]
    assert result.error == (
        "action '#DellRaidService.UnassignSpare' not found on "
        f"{RAID_SERVICE}"
    )
    assert _post_requests(service) == []


def test_dell_raid_spare_policy_and_registry_are_wired():
    """The command is registered and its actions are explicitly guarded."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidSpareActions]["dell-raid-spare"] is (
        DellRaidSpareActions
    )
    assert classify("#DellRaidService.AssignSpare") is Destructiveness.DESTRUCTIVE
    assert classify("#DellRaidService.UnassignSpare") is Destructiveness.DESTRUCTIVE

    cmd_parser, cmd_name, cmd_help = DellRaidSpareActions.register_subcommand(
        DellRaidSpareActions
    )
    help_text = cmd_parser.format_help()
    assert cmd_name == "dell-raid-spare"
    assert "spare" in cmd_help
    assert "--action" in help_text
    assert "--disk-fqdd" in help_text
    assert "--virtual-disk" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
