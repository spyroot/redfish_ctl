"""Dual-mode tests for boot-source mutation commands."""

import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_boot_source_enable_patches_matching_boot_option(redfish_mock, redfish_service):
    """boot-source-enable PATCHes the selected BootOption Enabled flag."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.EnableBootOptions,
        "boot_enable",
        boot_source="NIC.PxeDevice.1-1",
        is_enabled=False,
    )

    assert isinstance(result, CommandResult)
    json.dumps(result.data)

    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/bootoptions/nic.pxedevice.1-1"
    )
    assert request.json() == {"BootOptionEnabled": False}

    updated = redfish_mock.sync_invoke(
        ApiRequestType.QueryBootOption,
        "boot_source_query",
        boot_source="NIC.PxeDevice.1-1",
    )
    assert updated.data["NIC.PxeDevice.1-1"]["BootOptionEnabled"] is False
