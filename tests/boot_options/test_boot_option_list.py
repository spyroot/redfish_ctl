"""Dual-mode tests for the boot-sources list command (BootOptionsList).

    redfish_ctl boot-sources

Covers ``boot_sources_query`` (ApiRequestType.BootOptions), a read-only DMTF
BootOptionCollection walk. Runs offline against the mock service by default and
against real hardware when IDRAC_IP is set.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_boot_sources_query_returns_member_uris(redfish_api):
    """boot_sources_query flattens the BootOption collection to member URIs."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootOptions,
        "boot_sources_query",
    )
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.data == [
        "/redfish/v1/Systems/System.Embedded.1/BootOptions/HardDisk.List.1-1",
        "/redfish/v1/Systems/System.Embedded.1/BootOptions/NIC.PxeDevice.1-1",
    ]


def test_boot_sources_query_keeps_raw_collection_in_extra(redfish_api):
    """The raw BootOptionCollection body is preserved in CommandResult.extra."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootOptions,
        "boot_sources_query",
    )
    assert isinstance(result.extra, dict)
    assert result.extra["@odata.type"] == "#BootOptionCollection.BootOptionCollection"
    assert result.extra["Members@odata.count"] == 2
    assert result.error is None
