"""Offline tests for the iLO coverage-gap batch (new commands + Dell-locked reroutes).

Proves five capabilities work on the HPE iLO tree (tests/hpe_fixtures/) that the
coverage audit flagged as missing or Dell-locked:

- logs                (new)     — LogService/LogEntry reader
- ethernet-interfaces (new)     — host/BMC NIC config
- pci                 (reroute) — falls back to Chassis PCIeDevices (iLO layout)
- bios-registry       (reroute) — resolves the AttributeRegistry via /Registries/
- virtual-media       (reroute) — discovers the collection under Managers (iLO layout)

Dell backward-compat for the reroutes is covered by the existing dual-mode tests,
which still pass.
"""
from redfish_ctl.command_shared import ApiRequestType


def test_logs_reads_ilo_entries(redfish_mock_factory):
    """logs walks iLO LogServices (IML/SL/Event) and returns entries."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.Logs, "logs", limit=10)
    assert isinstance(result.data, list) and result.data, "no iLO log entries"
    row = result.data[0]
    assert row["Service"] and row["Source"]
    assert "Message" in row


def test_ethernet_interfaces_on_ilo(redfish_mock_factory):
    """ethernet-interfaces returns host/BMC NICs with identifying fields on iLO."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.EthernetInterfaces, "ethernet-interfaces")
    assert isinstance(result.data, list) and result.data
    assert all(r["Id"] and r["Source"] for r in result.data)


def test_pci_falls_back_to_chassis_on_ilo(redfish_mock_factory):
    """pci finds PCIeDevices under Chassis on iLO (Dell hangs them off the System)."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.PciDeviceQuery, "pci_device_query",
                             pci_type="PCIeDevices")
    assert isinstance(result.data, list) and result.data, "no PCIe devices via Chassis fallback"


def test_bios_registry_resolves_via_registries_on_ilo(redfish_mock_factory):
    """bios-registry resolves the AttributeRegistry name under /Registries/ on iLO."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.BiosRegistry, "bios_registry",
                             is_registry_only=True)
    # returns the registry's attribute entries (Dell path is absent on iLO)
    assert isinstance(result.data, list) and result.data


def test_virtual_media_discovered_under_managers_on_ilo(redfish_mock_factory):
    """virtual-media discovers the collection under a Manager on iLO."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.VirtualMediaGet, "virtual_disk_query")
    assert result.data is not None


def test_new_commands_dont_break_on_dell(redfish_mock):
    """logs/ethernet-interfaces degrade gracefully (no crash) on the Dell mock."""
    logs = redfish_mock.sync_invoke(ApiRequestType.Logs, "logs", limit=5)
    eth = redfish_mock.sync_invoke(ApiRequestType.EthernetInterfaces, "ethernet-interfaces")
    assert isinstance(logs.data, list)
    assert isinstance(eth.data, list)
