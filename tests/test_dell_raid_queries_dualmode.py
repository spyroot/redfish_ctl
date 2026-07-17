"""Dual-mode tests for DellRaidService query actions."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.raid.cmd_dell_raid_queries import DellRaidQueries
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
RAID_SERVICE = "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService"
AVAILABLE_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.GetAvailableDisks"
RAID_LEVELS_TARGET = f"{RAID_SERVICE}/Actions/DellRaidService.GetRAIDLevels"


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-dell-xr8620t",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _replace_post_response(service, status_code, body):
    """Replace the mock POST handler with a fixed response.

    :param service: mock Redfish service.
    :param status_code: HTTP status to return.
    :param body: JSON body to return.
    """
    requests_mock = pytest.importorskip("requests_mock")

    def post_cb(request, context):
        service.requests.append(request)
        context.status_code = status_code
        return json.dumps(body)

    service.mocker.post(requests_mock.ANY, text=post_cb)


def test_dell_raid_queries_list_corpus_targets_without_post(dell_corpus_mock):
    """Listing discovers read-only DellRaidService query actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {"available-disks", "dhs-disks", "raid-levels"}
    assert actions["available-disks"]["Resource"] == RAID_SERVICE
    assert actions["available-disks"]["Target"] == AVAILABLE_TARGET
    assert actions["available-disks"]["Parameters"]["DiskType"] == [
        "All",
        "HDD",
        "SSD",
    ]
    assert actions["raid-levels"]["Parameters"]["T10PIStatus"] == [
        "All",
        "T10PICapable",
        "T10PIIncapable",
    ]
    assert _post_requests(service) == []


def test_dell_raid_query_dry_run_resolves_payload_without_post(dell_corpus_mock):
    """--dry_run resolves the target and validates the payload without POSTing."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
        query="available-disks",
        disk_type="SSD",
        disk_protocol="NVMe",
        raid_level="RAID1",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellRaidService.GetAvailableDisks"
    assert result.data["level"] == "read_only"
    assert result.data["target"] == AVAILABLE_TARGET
    assert result.data["payload"] == {
        "DiskType": "SSD",
        "Diskprotocol": "NVMe",
        "RaidLevel": "RAID1",
    }
    assert _post_requests(service) == []


def test_dell_raid_query_posts_read_only_action_by_default(dell_corpus_mock):
    """DellRaidService query actions are read-only, so selected queries POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
        query="raid-levels",
        disk_type="SSD",
        disk_protocol="SAS",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellRaidService.GetRAIDLevels"
    assert result.data["level"] == "read_only"
    assert result.data["target"] == RAID_LEVELS_TARGET

    posts = _post_requests(service)
    assert len(posts) == 1
    assert posts[0].path.lower() == RAID_LEVELS_TARGET.lower()
    assert posts[0].json() == {
        "DiskType": "SSD",
        "Diskprotocol": "SAS",
    }


def test_dell_raid_query_preserves_sync_json_response(dell_corpus_mock):
    """A synchronous Dell query action body is returned to callers."""
    manager, service = dell_corpus_mock
    _replace_post_response(
        service,
        200,
        {"RAIDLevels": ["RAID0", "RAID1"]},
    )

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
        query="raid-levels",
        disk_type="SSD",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["Status"] == "ok"
    assert result.data["response"] == {"RAIDLevels": ["RAID0", "RAID1"]}

    posts = _post_requests(service)
    assert len(posts) == 1
    assert posts[0].path.lower() == RAID_LEVELS_TARGET.lower()
    assert posts[0].json() == {"DiskType": "SSD"}


def test_dell_raid_query_rejects_invalid_allowable_value(dell_corpus_mock):
    """Advertised allowable values are enforced before any POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
        query="raid-levels",
        disk_type="Tape",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellRaidService.GetRAIDLevels DiskType: Tape; "
        "allowed: All, HDD, SSD"
    )
    assert result.data["validation_errors"][0]["parameter"] == "DiskType"
    assert _post_requests(service) == []


def test_dell_raid_query_rejects_unsupported_filter(dell_corpus_mock):
    """Query-specific payload keys are checked before transport validation."""
    manager, service = dell_corpus_mock

    with pytest.raises(InvalidArgument, match="dhs-disks does not accept: DiskType"):
        manager.sync_invoke(
            ApiRequestType.DellRaidQueries,
            "dell-raid-queries",
            query="dhs-disks",
            disk_type="SSD",
        )
    assert _post_requests(service) == []


def test_dell_raid_query_missing_target_reports_without_post(dell_corpus_mock):
    """A service missing the selected action reports available targets."""
    manager, service = dell_corpus_mock
    service._overlay[RAID_SERVICE.lower()] = {
        "@odata.id": RAID_SERVICE,
        "Actions": {
            "#DellRaidService.GetDHSDisks": {
                "target": f"{RAID_SERVICE}/Actions/DellRaidService.GetDHSDisks"
            }
        },
    }

    result = manager.sync_invoke(
        ApiRequestType.DellRaidQueries,
        "dell-raid-queries",
        query="available-disks",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell RAID query action not found: available-disks"
    assert result.data["action"] == "#DellRaidService.GetAvailableDisks"
    assert [row["Action"] for row in result.data["available"]] == ["dhs-disks"]
    assert _post_requests(service) == []


def test_dell_raid_queries_exposes_cli_entrypoint():
    """The dell-raid-queries command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellRaidQueries]["dell-raid-queries"] is (
        DellRaidQueries
    )

    cmd_parser, cmd_name, cmd_help = DellRaidQueries.register_subcommand(
        DellRaidQueries
    )

    assert "--query" in cmd_parser.format_help()
    assert cmd_name == "dell-raid-queries"
    assert "RAID" in cmd_help
