"""Offline tests: Dell OEM commands remain isolated from non-Dell managers.

boot-source-* (DellBootSources) and raid/storage-convert-* (DellRaidService) are
Dell OEM features with no iLO equivalent — the vendor-neutral paths are the
standard boot-options/change-boot-order and volumes/volume-init commands. These
tests prove the Dell-OEM readers are not registered on an iLO or Supermicro
manager. Dell behavior is covered by the existing dual-mode tests.
"""
import pytest

from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.redfish_api_common import ApiRequestType


def test_dell_oem_readers_are_not_registered_on_ilo(redfish_mock_factory):
    """Dell boot-source and RAID commands are absent from the iLO command set."""
    mgr, _ = redfish_mock_factory("hpe")
    cases = [
        (ApiRequestType.BootSourceRegistry, "boot_source_registry"),
        (ApiRequestType.BootSourcePending, "query_pending"),
        (ApiRequestType.BootSettingsQuery, "boot_settings_query"),
        (ApiRequestType.RaidServiceQuery, "raid_service_query"),
    ]
    for api, name in cases:
        with pytest.raises(UnsupportedAction, match=f"Unknown {name} command"):
            mgr.sync_invoke(api, name)


def test_raid_service_is_not_registered_on_supermicro(redfish_mock_factory):
    """DellRaidService is absent from the Supermicro command set."""
    mgr, _ = redfish_mock_factory("supermicro")
    with pytest.raises(UnsupportedAction, match="Unknown raid_service_query command"):
        mgr.sync_invoke(ApiRequestType.RaidServiceQuery, "raid_service_query")
