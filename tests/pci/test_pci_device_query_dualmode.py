"""Dual-mode test for the PCI device query command."""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_pci_device_query_returns_linked_pcie_device(redfish_api):
    """pci_device_query follows the selected PCIeDevices link."""
    result = redfish_api.sync_invoke(
        ApiRequestType.PciDeviceQuery, "pci_device_query"
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert result.data[0]["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/PCIeDevices/NIC.Slot.1-1"
    )
    assert result.data[0]["Id"] == "NIC.Slot.1-1"
