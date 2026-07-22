"""Dual-mode-style coverage for DellRaidService RenameVD."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.raid.cmd_dell_raid_rename_vd import DellRaidRenameVD
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
RENAME_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.RenameVD"


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
            is_override = (
                request.path.lower() == RAID_SERVICE.lower()
                and service_body is not None
            )
            if is_override:
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
            context.headers["Location"] = "/redfish/v1/TaskService/Tasks/rename-vd-1"
            return json.dumps({
                "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/rename-vd-1"}
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


def test_dell_raid_rename_vd_lists_target_without_posting(
    dell_raid_manager_factory,
):
    """With no payload, the command lists RenameVD and never POSTs."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_rename_vd_previews_by_default(dell_raid_manager_factory):
    """RenameVD previews by default and does not POST."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_rename_vd_confirm_posts(dell_raid_manager_factory):
    """--confirm POSTs RenameVD to the corpus-advertised target."""
    manager, requests = dell_raid_manager_factory()

    result = manager.sync_invoke(
        ApiRequestType.DellRaidRenameVD,
        "dell-raid-rename-vd",
        target_fqdd="Disk.Virtual.0",
        vd_name="data-vd",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.RenameVD"
    assert result.data["target"] == RENAME_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "rename-vd-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == RENAME_TARGET.lower()
    assert posts[0].json() == {"TargetFQDD": "Disk.Virtual.0", "Name": "data-vd"}


def test_dell_raid_rename_vd_dry_run_overrides_confirm(
    dell_raid_manager_factory,
):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, requests = dell_raid_manager_factory()

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
    assert _post_requests(requests) == []


def test_dell_raid_rename_vd_missing_payload_is_rejected(
    dell_raid_manager_factory,
):
    """The command rejects missing required payload before POST."""
    manager, requests = dell_raid_manager_factory()

    with pytest.raises(InvalidArgument, match="requires: Name"):
        manager.sync_invoke(
            ApiRequestType.DellRaidRenameVD,
            "dell-raid-rename-vd",
            target_fqdd="Disk.Virtual.0",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_dell_raid_rename_vd_missing_action_reports_available(
    dell_raid_manager_factory,
):
    """A DellRaidService without RenameVD reports the missing action."""
    service_body = _corpus_body(RAID_SERVICE)
    service_body["Actions"] = dict(service_body["Actions"])
    service_body["Actions"].pop("#DellRaidService.RenameVD")
    manager, requests = dell_raid_manager_factory(service_body=service_body)

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
    assert _post_requests(requests) == []


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
