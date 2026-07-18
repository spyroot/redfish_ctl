"""Dual-mode-style coverage for Dell RAID blink actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.raid.cmd_dell_raid_blink import DellRaidBlink
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
)
BLINK_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.BlinkTarget"
UNBLINK_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.UnBlinkTarget"


@pytest.fixture
def dell_raid_manager():
    """Serve the committed Dell XR8620t corpus over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-dell-raid",
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


def test_dell_raid_blink_lists_actions_and_drives_without_post(dell_raid_manager):
    """The default command lists blink targets and candidate drive FQDDs."""
    manager, service = dell_raid_manager

    result = manager.sync_invoke(
        ApiRequestType.DellRaidBlink,
        "dell-raid-blink",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == [{
        "System": "System.Embedded.1",
        "SystemUri": "/redfish/v1/Systems/System.Embedded.1",
        "Service": RAID_SERVICE,
        "Actions": [
            {
                "Operation": "blink",
                "Action": "#DellRaidService.BlinkTarget",
                "Target": BLINK_TARGET,
            },
            {
                "Operation": "unblink",
                "Action": "#DellRaidService.UnBlinkTarget",
                "Target": UNBLINK_TARGET,
            },
        ],
        "Drives": [
            {
                "FQDD": "PCIeSSD.Integrated.1-0",
                "Uri": (
                    "/redfish/v1/Systems/System.Embedded.1/Storage/"
                    "PCIeSSD.Integrated.1-C/Drives/PCIeSSD.Integrated.1-0"
                ),
                "Name": "Integrated PCIe SSD 1 Disk 0",
            },
            {
                "FQDD": "PCIeSSD.Integrated.1-1",
                "Uri": (
                    "/redfish/v1/Systems/System.Embedded.1/Storage/"
                    "PCIeSSD.Integrated.1-C/Drives/PCIeSSD.Integrated.1-1"
                ),
                "Name": "Integrated PCIe SSD 1 Disk 1",
            },
        ],
    }]
    assert _post_requests(service) == []


def test_dell_raid_blink_previews_by_default_when_target_is_given(
    dell_raid_manager,
):
    """A target FQDD still dry-runs until --confirm is supplied."""
    manager, service = dell_raid_manager

    result = manager.sync_invoke(
        ApiRequestType.DellRaidBlink,
        "dell-raid-blink",
        target_fqdd="PCIeSSD.Integrated.1-0",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellRaidService.BlinkTarget"
    assert result.data["target"] == BLINK_TARGET
    assert result.data["payload"] == {"TargetFQDD": "PCIeSSD.Integrated.1-0"}
    assert result.data["level"] == "reversible"
    assert result.data["operation"] == "blink"
    assert result.data["service"] == RAID_SERVICE
    assert result.data["target_fqdd"] == "PCIeSSD.Integrated.1-0"
    assert _post_requests(service) == []


def test_dell_raid_unblink_confirm_posts_target(dell_raid_manager):
    """--confirm sends one UnBlinkTarget POST with the TargetFQDD payload."""
    manager, service = dell_raid_manager

    result = manager.sync_invoke(
        ApiRequestType.DellRaidBlink,
        "dell-raid-blink",
        operation="unblink",
        target_fqdd="PCIeSSD.Integrated.1-1",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.UnBlinkTarget"
    assert result.data["target"] == UNBLINK_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["operation"] == "unblink"
    assert len(posts) == 1
    assert posts[0].path.lower() == UNBLINK_TARGET.lower()
    assert posts[0].json() == {"TargetFQDD": "PCIeSSD.Integrated.1-1"}


def test_dell_raid_blink_requires_target_when_confirming(dell_raid_manager):
    """--confirm without TargetFQDD is rejected before any POST."""
    manager, service = dell_raid_manager

    result = manager.sync_invoke(
        ApiRequestType.DellRaidBlink,
        "dell-raid-blink",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "TargetFQDD is required; rerun with --target-fqdd"
    assert result.data["matches"][0]["Service"] == RAID_SERVICE
    assert _post_requests(service) == []


def test_dell_raid_blink_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports missing DellRaidService actions."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellRaidBlink,
        "dell-raid-blink",
        target_fqdd="Disk.Bay.0",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "DellRaidService BlinkTarget/UnBlinkTarget actions not found"
    )
    assert result.data == {
        "actions": [
            "#DellRaidService.BlinkTarget",
            "#DellRaidService.UnBlinkTarget",
        ],
        "available": [],
    }
    assert _post_requests(service) == []


def test_dell_raid_blink_is_registered():
    """The dell-raid-blink command is wired into the registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellRaidBlink]["dell-raid-blink"] is DellRaidBlink

    cmd_parser, cmd_name, cmd_help = (
        DellRaidBlink.register_subcommand(DellRaidBlink)
    )

    assert cmd_name == "dell-raid-blink"
    assert "Dell RAID physical disk" in cmd_help
    help_text = cmd_parser.format_help()
    assert "--operation" in help_text
    assert "--target-fqdd" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text


def test_dell_raid_blink_policy_is_reversible():
    """Generic action listings classify Dell RAID identify actions as reversible."""
    assert classify("#DellRaidService.BlinkTarget") is Destructiveness.REVERSIBLE
    assert classify("#DellRaidService.UnBlinkTarget") is Destructiveness.REVERSIBLE
