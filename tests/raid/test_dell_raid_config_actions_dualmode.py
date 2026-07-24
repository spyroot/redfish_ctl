"""Dual-mode-style coverage for DellRaidService configuration actions."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_config_actions import DellRaidConfigActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
BOOT_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.SetBootVD"
ASSET_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.SetAssetName"


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


def test_dell_raid_config_actions_list_targets_without_posting(
    dell_raid_mock,
):
    """With no action, the command lists supported targets and never POSTs."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
    )

    rows = {row["Action"]: row for row in result.data}
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert rows["set-boot-vd"]["Target"] == BOOT_TARGET
    assert rows["set-boot-vd"]["RequiredPayload"] == ["TargetFQDD"]
    assert rows["set-asset-name"]["Target"] == ASSET_TARGET
    assert rows["set-asset-name"]["RequiredPayload"] == ["AssetName"]
    assert _post_requests(service) == []


def test_dell_raid_config_actions_previews_boot_vd_by_default(
    dell_raid_mock,
):
    """SetBootVD previews by default and does not POST."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
        action="set-boot-vd",
        target_fqdd="Disk.Virtual.0",
    )

    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": "#DellRaidService.SetBootVD",
        "target": BOOT_TARGET,
        "payload": {"TargetFQDD": "Disk.Virtual.0"},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(service) == []


def test_dell_raid_config_actions_confirm_posts_asset_name(
    dell_raid_mock,
):
    """--confirm POSTs SetAssetName; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
        action="set-asset-name",
        asset_name="rack-a-drawer-2",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.SetAssetName"
    assert result.data["target"] == ASSET_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == ASSET_TARGET.lower()
    assert posts[0].json() == {"AssetName": "rack-a-drawer-2"}


def test_dell_raid_config_actions_dry_run_overrides_confirm(
    dell_raid_mock,
):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_raid_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
        action="set-boot-vd",
        target_fqdd="Disk.Virtual.0",
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == BOOT_TARGET
    assert _post_requests(service) == []


def test_dell_raid_config_actions_missing_payload_is_rejected(
    dell_raid_mock,
):
    """The command rejects a selected action before POST when required payload is missing."""
    manager, service = dell_raid_mock

    with pytest.raises(InvalidArgument, match="set-boot-vd requires: TargetFQDD"):
        manager.sync_invoke(
            ApiRequestType.DellRaidConfigActions,
            "dell-raid-config-actions",
            action="set-boot-vd",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_dell_raid_config_actions_missing_action_reports_available(
    dell_raid_mock,
):
    """A DellRaidService without SetBootVD reports the missing action."""
    manager, service = dell_raid_mock
    body = copy.deepcopy(service._state(RAID_SERVICE))
    body["Actions"].pop("#DellRaidService.SetBootVD")
    _overlay_raid_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
        action="set-boot-vd",
        target_fqdd="Disk.Virtual.0",
        confirm=True,
    )

    assert result.error == "Dell RAID configuration action not found: set-boot-vd"
    assert result.data["action"] == "#DellRaidService.SetBootVD"
    assert result.data["available"][0]["Action"] == "set-asset-name"
    assert _post_requests(service) == []


def test_dell_raid_config_actions_policy_and_registry():
    """Configuration actions are classified and the command is registered."""
    assert classify("#DellRaidService.SetAssetName") is Destructiveness.DESTRUCTIVE
    assert classify("#DellRaidService.SetBootVD") is Destructiveness.DESTRUCTIVE

    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellRaidConfigActions][
        "dell-raid-config-actions"
    ] is DellRaidConfigActions

    cmd_parser, cmd_name, cmd_help = DellRaidConfigActions.register_subcommand(
        DellRaidConfigActions
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-raid-config-actions"
    assert "Dell RAID configuration" in cmd_help
    assert "--target-fqdd" in help_text
    assert "--asset-name" in help_text
