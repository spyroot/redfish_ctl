"""Offline test for the vendor-neutral network-ports command.

Walks Chassis -> NetworkAdapters -> Ports and returns per-port link state. Verified
on the GB300 and HPE iLO corpora; this is the per-port view network-adapters omits.
"""
from redfish_ctl.command_shared import ApiRequestType


def test_network_ports_supermicro(redfish_mock_factory):
    """network-ports returns per-adapter port link state on the GB300 tree."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.NetworkPorts, "network-ports")
    assert isinstance(result.data, list) and result.data
    row = result.data[0]
    assert row["Chassis"] and row["Adapter"] and row["Port"]
    # ports carry link/speed fields (some may be None depending on the port)
    assert "LinkStatus" in row and "CurrentSpeedGbps" in row


def test_network_ports_ilo(redfish_mock_factory):
    """network-ports also resolves on the iLO tree (vendor-neutral)."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.NetworkPorts, "network-ports")
    assert isinstance(result.data, list) and result.data
    assert all(r["Adapter"] and r["Port"] for r in result.data)
