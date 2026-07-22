"""Dual-mode-style coverage for DellRaidService physical-disk actions."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.raid.cmd_pd_state import DellRaidPhysicalDiskActions
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
STATE_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.ChangePDState"
PREPARE_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.PrepareToRemove"
REBUILD_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.RebuildPhysicalDisk"
DISK_FQDD = "Disk.Bay.0:Enclosure.Internal.0-1:RAID.Integrated.1-1"


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-pd-actions",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _without_action(action_name):
    """Return the DellRaidService fixture body with one action removed."""
    fixture = DELL_CORPUS / "_redfish_v1_Systems_System.Embedded.1_Oem_Dell_DellRaidService.json"
    body = json.loads(fixture.read_text())
    body["Actions"] = dict(body["Actions"])
    body["Actions"].pop(action_name, None)
    return body


def test_dell_raid_pd_actions_list_targets_without_post(dell_corpus_mock):
    """Calling without an action lists advertised physical-disk actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["raid_service"] == RAID_SERVICE
    actions = {row["Action"]: row for row in result.data["actions"]}
    assert set(actions) == {"prepare-remove", "rebuild", "state"}
    assert actions["state"]["Target"] == STATE_TARGET
    assert actions["state"]["Parameters"]["State"] == ["Offline", "Online"]
    assert actions["prepare-remove"]["Target"] == PREPARE_TARGET
    assert actions["prepare-remove"]["Parameters"]["ForceRemove"] == ["No", "Yes"]
    assert actions["rebuild"]["Target"] == REBUILD_TARGET
    assert any(
        drive["id"] == "PCIeSSD.Integrated.1-0"
        for drive in result.data["candidates"]
    )
    assert _post_requests(service) == []


def test_dell_raid_pd_state_previews_by_default(dell_corpus_mock):
    """ChangePDState is destructive and does not POST without --confirm."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
        action="state",
        disk_fqdd=DISK_FQDD,
        state="Online",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.ChangePDState",
        "target": STATE_TARGET,
        "payload": {"TargetFQDD": DISK_FQDD, "State": "Online"},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(service) == []


def test_dell_raid_pd_prepare_posts_with_confirm(dell_corpus_mock):
    """--confirm POSTs PrepareToRemove to the corpus-advertised action target."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
        action="prepare-remove",
        disk_fqdd=DISK_FQDD,
        force_remove="Yes",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.PrepareToRemove"
    assert result.data["target"] == PREPARE_TARGET
    assert result.data["task_id"] == MockRedfishService.JOB_ID
    assert len(posts) == 1
    assert posts[0].path.lower() == PREPARE_TARGET.lower()
    assert posts[0].json() == {
        "TargetFQDD": DISK_FQDD,
        "ForceRemove": "Yes",
    }


def test_dell_raid_pd_rebuild_dry_run_overrides_confirm(dell_corpus_mock):
    """--dry_run keeps RebuildPhysicalDisk as a no-POST preview with --confirm."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
        action="rebuild",
        disk_fqdd=DISK_FQDD,
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["action"] == "#DellRaidService.RebuildPhysicalDisk"
    assert result.data["target"] == REBUILD_TARGET
    assert result.data["payload"] == {"TargetFQDD": DISK_FQDD}
    assert _post_requests(service) == []


def test_dell_raid_pd_state_rejects_invalid_allowable_value(dell_corpus_mock):
    """Advertised State allowable values are enforced before any POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
        action="state",
        disk_fqdd=DISK_FQDD,
        state="Failed",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellRaidService.ChangePDState State: Failed; "
        "allowed: Offline, Online"
    )
    assert result.data["validation_errors"][0]["parameter"] == "State"
    assert _post_requests(service) == []


def test_dell_raid_pd_missing_disk_is_rejected_before_post(dell_corpus_mock):
    """A selected action requires a physical disk FQDD."""
    manager, service = dell_corpus_mock

    with pytest.raises(InvalidArgument, match="disk FQDD cannot be empty"):
        manager.sync_invoke(
            ApiRequestType.DellRaidPhysicalDiskActions,
            "dell-raid-pd-actions",
            action="rebuild",
            confirm=True,
        )
    assert _post_requests(service) == []


def test_dell_raid_pd_missing_action_reports_available(dell_corpus_mock):
    """A service without the selected action returns an actionable no-POST error."""
    manager, service = dell_corpus_mock
    service._overlay[RAID_SERVICE.lower()] = _without_action(
        "#DellRaidService.RebuildPhysicalDisk"
    )

    result = manager.sync_invoke(
        ApiRequestType.DellRaidPhysicalDiskActions,
        "dell-raid-pd-actions",
        action="rebuild",
        disk_fqdd=DISK_FQDD,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data["action"] == "#DellRaidService.RebuildPhysicalDisk"
    assert result.error == (
        "action '#DellRaidService.RebuildPhysicalDisk' not found on "
        f"{RAID_SERVICE}"
    )
    assert _post_requests(service) == []


def test_dell_raid_pd_policy_and_registry_are_wired():
    """The command is registered and its actions are explicitly guarded."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidPhysicalDiskActions][
        "dell-raid-pd-actions"
    ] is DellRaidPhysicalDiskActions

    for action in (
        "#DellRaidService.ChangePDState",
        "#DellRaidService.PrepareToRemove",
        "#DellRaidService.RebuildPhysicalDisk",
    ):
        assert classify(action) is Destructiveness.DESTRUCTIVE

    cmd_parser, cmd_name, cmd_help = DellRaidPhysicalDiskActions.register_subcommand(
        DellRaidPhysicalDiskActions
    )
    help_text = cmd_parser.format_help()
    assert cmd_name == "dell-raid-pd-actions"
    assert "physical disk" in cmd_help
    assert "--action" in help_text
    assert "--disk-fqdd" in help_text
    assert "--state" in help_text
    assert "--force-remove" in help_text
    assert "--confirm" in help_text
