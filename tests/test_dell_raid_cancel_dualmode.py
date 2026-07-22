"""Dual-mode tests for DellRaidService cancel actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_cancel import DellRaidCancelActions
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
ACTION_BASE = f"{RAID_SERVICE}/Actions/DellRaidService"


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
                idrac_ip="mock-dell-xr8620t",
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


def test_dell_raid_cancel_lists_corpus_targets_without_post(dell_corpus_mock):
    """Listing discovers the DellRaidService cancel actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {
        "background-init",
        "check-consistency",
        "rebuild-physical-disk",
    }
    assert actions["background-init"]["Resource"] == RAID_SERVICE
    assert actions["background-init"]["FullType"] == (
        "#DellRaidService.CancelBackgroundInitialization"
    )
    assert actions["background-init"]["Target"] == (
        f"{ACTION_BASE}.CancelBackgroundInitialization"
    )
    assert actions["check-consistency"]["FullType"] == (
        "#DellRaidService.CancelCheckConsistency"
    )
    assert actions["check-consistency"]["Target"] == (
        f"{ACTION_BASE}.CancelCheckConsistency"
    )
    assert actions["rebuild-physical-disk"]["FullType"] == (
        "#DellRaidService.CancelRebuildPhysicalDisk"
    )
    assert actions["rebuild-physical-disk"]["Target"] == (
        f"{ACTION_BASE}.CancelRebuildPhysicalDisk"
    )
    assert _post_requests(service) == []


def test_dell_raid_cancel_previews_selected_action_by_default(dell_corpus_mock):
    """A selected cancel action resolves the target but does not POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="check-consistency",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellRaidService.CancelCheckConsistency"
    assert result.data["level"] == "destructive"
    assert result.data["target"] == f"{ACTION_BASE}.CancelCheckConsistency"
    assert result.data["payload"] == {}
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_dell_raid_cancel_confirm_posts_selected_target(dell_corpus_mock):
    """--confirm POSTs exactly one selected Dell RAID cancel action."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="rebuild-physical-disk",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.CancelRebuildPhysicalDisk"
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        f"{ACTION_BASE}.CancelRebuildPhysicalDisk".lower()
    )
    assert posts[0].json() == {}


def test_dell_raid_cancel_dry_run_overrides_confirm(dell_corpus_mock):
    """--dry_run keeps a selected action in preview mode even with --confirm."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="background-init",
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == (
        f"{ACTION_BASE}.CancelBackgroundInitialization"
    )
    assert _post_requests(service) == []


def test_dell_raid_cancel_resource_uri_filters_target(dell_corpus_mock):
    """--resource-uri can select the discovered DellRaidService resource."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="check-consistency",
        resource_uri=RAID_SERVICE,
    )

    assert result.error is None
    assert result.data["target"] == f"{ACTION_BASE}.CancelCheckConsistency"
    assert _post_requests(service) == []


def test_dell_raid_cancel_mismatched_resource_uri_reports_available(
    dell_corpus_mock,
):
    """A mismatched --resource-uri does not POST and reports discovered targets."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="check-consistency",
        resource_uri="/redfish/v1/Systems/System.Embedded.1/Oem/Dell/Nope",
        confirm=True,
    )

    assert result.error == "Dell RAID cancel action not found: check-consistency"
    assert len(result.data["available"]) == 3
    assert _post_requests(service) == []


def test_dell_raid_cancel_invalid_selector_reports_available(dell_corpus_mock):
    """Direct API callers still get a safe error for invalid action selectors."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="not-a-real-selector",
        confirm=True,
    )

    assert result.error == "Dell RAID cancel action not found: not-a-real-selector"
    assert len(result.data["available"]) == 3
    assert _post_requests(service) == []


def test_dell_raid_cancel_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A fixture without Dell RAID cancel actions reports an error."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellRaidCancelActions,
        "dell-raid-cancel-actions",
        action="check-consistency",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell RAID cancel action not found: check-consistency"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_dell_raid_cancel_exposes_cli_entrypoint_and_policy():
    """The dell-raid-cancel-actions command is registered and classified."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidCancelActions][
        "dell-raid-cancel-actions"
    ] is DellRaidCancelActions

    cmd_parser, cmd_name, cmd_help = DellRaidCancelActions.register_subcommand(
        DellRaidCancelActions
    )
    help_text = cmd_parser.format_help()

    assert classify(
        "#DellRaidService.CancelBackgroundInitialization"
    ) is Destructiveness.DESTRUCTIVE
    assert classify(
        "#DellRaidService.CancelCheckConsistency"
    ) is Destructiveness.DESTRUCTIVE
    assert classify(
        "#DellRaidService.CancelRebuildPhysicalDisk"
    ) is Destructiveness.DESTRUCTIVE
    assert "--action" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text
    assert cmd_name == "dell-raid-cancel-actions"
    assert "Dell RAID" in cmd_help
