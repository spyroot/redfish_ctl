"""Dual-mode tests for the read-only network-ports command."""

import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_network_ports_returns_dell_port_rows_without_mutation(
    redfish_api,
    redfish_service,
):
    """network-ports walks NetworkAdapter Ports and never writes in mock mode."""
    result = redfish_api.sync_invoke(ApiRequestType.NetworkPorts, "network-ports")

    assert isinstance(result, CommandResult)
    assert result.data == [
        {
            "Chassis": "NetworkFabric.1",
            "Adapter": "NIC.Integrated.1-1",
            "Port": "NIC.Integrated.1-1-1",
            "LinkStatus": "LinkUp",
            "LinkState": "Enabled",
            "LinkNetworkTechnology": "Ethernet",
            "CurrentSpeedGbps": 25,
            "MaxSpeedGbps": 25,
        }
    ]
    json.dumps(result.data, sort_keys=True)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert all(
        recorded.method not in {"POST", "PATCH", "DELETE"}
        for recorded in redfish_service.requests
    )
