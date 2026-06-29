"""Dual-mode tests for the boot-source registry command."""

import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_boot_source_registry_returns_attribute_registry(redfish_api):
    """boot_source_registry reads the BootSourcesRegistry resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootSourceRegistry,
        "boot_source_registry",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/BootSources/BootSourcesRegistry"
    )
    assert result.data["OwningEntity"] == "Dell"
    entries = {
        item["AttributeName"]: item
        for item in result.data["RegistryEntries"]["Attributes"]
    }
    assert entries["BootMode"]["Type"] == "Enumeration"
    assert [value["ValueName"] for value in entries["BootMode"]["Value"]] == [
        "Bios",
        "Uefi",
    ]
