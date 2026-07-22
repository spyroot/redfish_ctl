"""Dual-mode-style coverage for DellRaidService configuration actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_config_actions import DellRaidConfigActions
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
BOOT_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.SetBootVD"
ASSET_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.SetAssetName"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path."""
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _corpus_body(path):
    """Return one Dell corpus fixture body as JSON."""
    fixture = _fixture_for_path(path)
    if fixture is None:
        raise AssertionError(f"missing Dell fixture for {path}")
    return json.loads(fixture.read_text())


@pytest.fixture
def dell_raid_manager_factory():
    """Serve the committed Dell corpus over requests-mock."""
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
            context.headers["Location"] = "/redfish/v1/TaskService/Tasks/raid-config-1"
            return json.dumps({
                "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/raid-config-1"}
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
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def test_dell_raid_config_actions_list_targets_without_posting(
    dell_raid_manager_factory,
):
    """With no action, the command lists supported targets and never POSTs."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_config_actions_previews_boot_vd_by_default(
    dell_raid_manager_factory,
):
    """SetBootVD previews by default and does not POST."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_config_actions_confirm_posts_asset_name(
    dell_raid_manager_factory,
):
    """--confirm POSTs SetAssetName to the corpus-advertised target."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidConfigActions,
        "dell-raid-config-actions",
        action="set-asset-name",
        asset_name="rack-a-drawer-2",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.SetAssetName"
    assert result.data["target"] == ASSET_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "raid-config-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == ASSET_TARGET.lower()
    assert posts[0].json() == {"AssetName": "rack-a-drawer-2"}


def test_dell_raid_config_actions_dry_run_overrides_confirm(
    dell_raid_manager_factory,
):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_config_actions_missing_payload_is_rejected(
    dell_raid_manager_factory,
):
    """The command rejects a selected action before POST when required payload is missing."""
    manager, requests = dell_raid_manager_factory()

    with pytest.raises(InvalidArgument, match="set-boot-vd requires: TargetFQDD"):
        manager.sync_invoke(
            ApiRequestType.DellRaidConfigActions,
            "dell-raid-config-actions",
            action="set-boot-vd",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_dell_raid_config_actions_missing_action_reports_available(
    dell_raid_manager_factory,
):
    """A DellRaidService without SetBootVD reports the missing action."""
    service_body = _corpus_body(RAID_SERVICE)
    service_body["Actions"] = dict(service_body["Actions"])
    service_body["Actions"].pop("#DellRaidService.SetBootVD")
    manager, requests = dell_raid_manager_factory(service_body=service_body)

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
    assert _post_requests(requests) == []


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
