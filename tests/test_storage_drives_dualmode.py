"""Dual-mode test for the storage-drives query command.

Runs offline by default against the mock service (using the iDRAC-shaped Storage
fixture in tests/idrac_fixtures/, which carries a Drives navigation list), and
against real hardware when IDRAC_IP is set. Modeled on test_cmd_boot_dualmode.py.

This is also a regression test for a missing import: cmd_drives.py walks the
Storage payload with find_ids(...) but never imported it, so the offline
'storage-drives' path raised NameError before the import was added. The tests
drive the command through sync_invoke and assert a CommandResult comes back
without a NameError.

Author Mus spyroot@gmail.com
"""
import json

import pytest

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

# The controller + drive the Storage fixture's Drives navigation list points at.
_CONTROLLER = "RAID.Integrated.1-1"
_DRIVE_PATH = (
    "/redfish/v1/Systems/System.Embedded.1/Storage/"
    "RAID.Integrated.1-1/Drives/Disk.Bay.0"
)
_CONVERT_TO_RAID_PATH = (
    "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService/"
    "Actions/DellRaidService.ConvertToRAID"
)
_CONVERT_TO_NONRAID_PATH = (
    "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService/"
    "Actions/DellRaidService.ConvertToNonRAID"
)


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _assert_convert_to_raid_post(service, pd_array):
    """Assert ConvertToRaid sent the expected Dell RAID action request."""
    posts = _post_requests(service)
    assert len(posts) == 1
    request = posts[0]
    assert request.path.lower() == _CONVERT_TO_RAID_PATH.lower()
    assert request.json() == {"PDArray": pd_array}


def _assert_convert_to_nonraid_post(service, pd_array):
    """Assert ConvertNoneRaid sent the expected Dell RAID action request."""
    posts = _post_requests(service)
    assert len(posts) == 1
    request = posts[0]
    assert request.path.lower() == _CONVERT_TO_NONRAID_PATH.lower()
    assert request.json() == {"PDArray": pd_array}


def _seed_drive(service, raid_status: str) -> None:
    """Seed one physical-disk resource so the per-drive loop completes offline.

    The Storage fixture only carries a Drives *navigation* list (an @odata.id per
    drive); the captured tree has no body for the drive itself, so without a seed
    the loop's base_query would 404. The body mirrors the Dell OEM shape
    cmd_drives classifies on (Oem.Dell.DellPhysicalDisk.RaidStatus).

    requests-mock lowercases request.path, and MockRedfishService keys its overlay
    by that path, so we seed under the lowercased key to match the GET lookup.
    """
    service._overlay[_DRIVE_PATH.lower()] = {
        "@odata.id": _DRIVE_PATH,
        "Id": "Disk.Bay.0",
        "Oem": {"Dell": {"DellPhysicalDisk": {"RaidStatus": raid_status}}},
    }


def test_storage_drives_returns_command_result_without_nameerror(
    redfish_mock, redfish_service
):
    """storage-drives runs end to end and returns a JSON-serializable CommandResult.

    Regression guard: cmd_drives calls find_ids(...) on the Storage payload; a
    missing import made this raise NameError on the offline path. Reaching this
    assertion proves the symbol resolves and the command completes.
    """
    _seed_drive(redfish_service, "Online")

    result = redfish_mock.sync_invoke(
        ApiRequestType.Drives, "drives_query", controller=_CONTROLLER
    )
    assert isinstance(result, CommandResult)
    # The CLI renders the payload as JSON, so it must be serializable.
    json.dumps(result.data)


def test_storage_drives_walks_drives_navigation_list_in_mock_mode(
    redfish_mock, redfish_service
):
    """storage-drives follows the Storage Drives list and classifies a RAID disk.

    Exercises the full offline path: storage_get returns the Storage resource
    (which has a Drives navigation list), find_ids extracts the drive @odata.id,
    the per-drive query reads the seeded disk, and the disk is bucketed as a
    RAID member because its RaidStatus is not 'NonRAID'.
    """
    _seed_drive(redfish_service, "Online")

    result = redfish_mock.sync_invoke(
        ApiRequestType.Drives, "drives_query", controller=_CONTROLLER
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    # The trailing bookkeeping dicts cmd_drives appends for the discovered disks.
    disk_ids = next(d["disk_ids"] for d in result.data if "disk_ids" in d)
    raid_ids = next(d["raid_disk_ids"] for d in result.data if "raid_disk_ids" in d)
    none_raid_ids = next(
        d["none_raid_disk_ids"] for d in result.data if "none_raid_disk_ids" in d
    )
    assert "Disk.Bay.0" in disk_ids
    assert disk_ids["Disk.Bay.0"] == _DRIVE_PATH
    assert raid_ids == ["Disk.Bay.0"]
    assert none_raid_ids == []


def test_storage_drives_buckets_non_raid_disk_in_mock_mode(
    redfish_mock, redfish_service
):
    """A disk whose RaidStatus contains 'NonRAID' lands in none_raid_disk_ids.

    The edge case mirrors a freshly converted / un-configured physical disk that
    iDRAC reports as NonRAID; cmd_drives must bucket it separately from RAID
    members.
    """
    _seed_drive(redfish_service, "NonRAID")

    result = redfish_mock.sync_invoke(
        ApiRequestType.Drives, "drives_query", controller=_CONTROLLER
    )

    none_raid_ids = next(
        d["none_raid_disk_ids"] for d in result.data if "none_raid_disk_ids" in d
    )
    raid_ids = next(d["raid_disk_ids"] for d in result.data if "raid_disk_ids" in d)
    assert none_raid_ids == ["Disk.Bay.0"]
    assert raid_ids == []


def test_storage_convert_noraid_skips_post_when_disks_are_nonraid_in_mock_mode(
    redfish_mock, redfish_service
):
    """storage-convert-noraid returns a no-op result when every disk is NonRAID."""
    _seed_drive(redfish_service, "NonRAID")

    result = redfish_mock.sync_invoke(
        ApiRequestType.ConvertNoneRaid,
        "convert_none_raid",
        controller=_CONTROLLER,
        exclude_filter="",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": "all disk are none raid"}
    assert all(request.method != "POST" for request in redfish_service.requests)


def test_storage_convert_noraid_posts_raid_pdarray_in_mock_mode(
    redfish_mock, redfish_service
):
    """storage-convert-noraid builds a ConvertToNonRAID request for RAID disks."""
    _seed_drive(redfish_service, "Online")

    result = redfish_mock.sync_invoke(
        ApiRequestType.ConvertNoneRaid,
        "convert_none_raid",
        controller=_CONTROLLER,
        exclude_filter="",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    _assert_convert_to_nonraid_post(redfish_service, ["Disk.Bay.0"])


def test_storage_convert_to_raid_skips_post_when_no_nonraid_disks_in_mock_mode(
    redfish_mock, redfish_service
):
    """storage-convert-raid is a no-op when no physical disk is NonRAID."""
    _seed_drive(redfish_service, "Online")

    result = redfish_mock.sync_invoke(
        ApiRequestType.ConvertToRaid,
        "convert_none_raid",
        controller=_CONTROLLER,
        exclude_filter="",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": "all disk are none raid"}
    assert _post_requests(redfish_service) == []


def test_storage_convert_to_raid_posts_nonraid_pdarray_in_mock_mode(
    redfish_mock, redfish_service
):
    """storage-convert-raid builds a ConvertToRAID request for NonRAID disks."""
    _seed_drive(redfish_service, "NonRAID")

    result = redfish_mock.sync_invoke(
        ApiRequestType.ConvertToRaid,
        "convert_none_raid",
        controller=_CONTROLLER,
        exclude_filter="",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    _assert_convert_to_raid_post(redfish_service, ["Disk.Bay.0"])


@pytest.mark.live
def test_storage_drives_live_returns_command_result(redfish_api):
    """Against real hardware, storage-drives returns a CommandResult.

    Skipped unless IDRAC_IP is set (see conftest). Read-only: it only queries the
    storage subsystem. Uses the default controller discovery rather than a
    hardcoded controller id, since live drive ids vary per box.
    """
    result = redfish_api.sync_invoke(ApiRequestType.Drives, "drives_query")
    assert isinstance(result, CommandResult)
    json.dumps(result.data)
