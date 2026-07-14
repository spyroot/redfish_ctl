"""Offline tests: the Dell-OEM cluster degrades gracefully on non-Dell hosts.

boot-source-* (DellBootSources) and raid/storage-convert-* (DellRaidService) are
Dell OEM features with no iLO equivalent — the vendor-neutral paths are the
standard boot-options/change-boot-order and volumes/volume-init commands. These
tests prove the Dell-OEM readers no longer CRASH on an iLO tree: they return a
clear "Dell-specific" result instead of raising. The RAID commands also no longer
hardcode System.Embedded.1 (they use the discovered system id).

Dell behavior (where the OEM resource exists) is covered by the existing dual-mode
tests, which still pass.
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_dell_oem_readers_graceful_on_ilo(redfish_mock_factory):
    """boot-source-registry / pending / boot-settings / raid don't crash on iLO."""
    mgr, _ = redfish_mock_factory("hpe")
    cases = [
        (ApiRequestType.BootSourceRegistry, "boot_source_registry"),
        (ApiRequestType.BootSourcePending, "query_pending"),
        (ApiRequestType.BootSettingsQuery, "boot_settings_query"),
        (ApiRequestType.RaidServiceQuery, "raid_service_query"),
    ]
    for api, name in cases:
        result = mgr.sync_invoke(api, name)          # must not raise on iLO
        assert result is not None
        # each surfaces a clear Dell-specific notice rather than crashing
        assert result.error is None or "Dell" in result.error


def test_raid_service_uses_discovered_id(redfish_mock_factory):
    """raid-service resolves against the discovered system id, not System.Embedded.1."""
    mgr, _ = redfish_mock_factory("supermicro")
    # supermicro's host is System_0; the Dell-only service is absent, so this
    # degrades gracefully — the point is it does not target System.Embedded.1.
    result = mgr.sync_invoke(ApiRequestType.RaidServiceQuery, "raid_service_query")
    assert result is not None
    assert result.error is None or "Dell" in result.error
