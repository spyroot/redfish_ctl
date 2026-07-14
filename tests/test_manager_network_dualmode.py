"""Dual-mode tests for the manager-network command."""
import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_manager_network_reads_gb300_protocols_and_ntp(redfish_mock_factory):
    """manager-network summarizes GB300 ManagerNetworkProtocol resources."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ManagerNetworkProtocol,
        "manager-network",
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    rows_by_manager = {row["Manager"]: row for row in result.data}
    assert set(rows_by_manager) == {"BMC_0", "HGX_BMC_0"}

    bmc = rows_by_manager["BMC_0"]
    assert bmc["HostName"] == "GBNVL"
    assert bmc["HTTP"] == {"ProtocolEnabled": False, "Port": None}
    assert bmc["HTTPS"] == {"ProtocolEnabled": True, "Port": 443}
    assert bmc["IPMI"] == {"ProtocolEnabled": True, "Port": 623}
    assert bmc["SSH"] == {"ProtocolEnabled": True, "Port": 22}
    assert bmc["NTP"] == {"ProtocolEnabled": True, "NTPServers": []}

    hgx = rows_by_manager["HGX_BMC_0"]
    assert hgx["HTTP"] == {"ProtocolEnabled": True, "Port": 80}
    assert hgx["HTTPS"] == {"ProtocolEnabled": False, "Port": None}
    assert hgx["IPMI"] == {"ProtocolEnabled": None, "Port": None}
    assert hgx["NTP"] == {"ProtocolEnabled": None, "NTPServers": []}

    assert {
        request.method
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()


def test_manager_network_tolerates_x10_without_ntp_block(redfish_mock_factory):
    """manager-network keeps X10 NetworkProtocol usable when NTP is absent."""
    manager, service = redfish_mock_factory("supermicro_x10_119")

    result = manager.sync_invoke(
        ApiRequestType.ManagerNetworkProtocol,
        "manager-network",
    )

    assert isinstance(result, CommandResult)
    assert len(result.data) == 1

    row = result.data[0]
    assert row["Manager"] == "1"
    assert row["HostName"] == "blade02"
    assert row["HTTP"] == {"ProtocolEnabled": True, "Port": 80}
    assert row["HTTPS"] == {"ProtocolEnabled": True, "Port": 443}
    assert row["IPMI"] == {"ProtocolEnabled": True, "Port": 623}
    assert row["SSH"] == {"ProtocolEnabled": True, "Port": 22}
    assert row["NTP"] == {"ProtocolEnabled": None, "NTPServers": []}

    assert {
        request.method
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
