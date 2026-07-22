"""Dual-mode-style tests for the generic ethernet-interfaces command."""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_ethernet_interfaces_reads_hpe_host_and_manager_rows_without_mutation(
    redfish_mock_factory,
):
    """ethernet-interfaces walks HPE System and Manager NIC collections."""
    manager, service = redfish_mock_factory("hpe")

    result = manager.sync_invoke(
        ApiRequestType.EthernetInterfaces,
        "ethernet-interfaces",
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert len(result.data) == 5
    rows_by_id = {row["Id"]: row for row in result.data}
    assert set(rows_by_id) == {"77", "78", "1", "2", "3"}
    assert rows_by_id["77"] == {
        "Source": "1",
        "Id": "77",
        "Name": "",
        "MACAddress": "58:a2:e1:03:96:f2",
        "LinkStatus": "LinkUp",
        "SpeedMbps": None,
        "IPv4": None,
        "Health": "OK",
    }
    assert rows_by_id["78"] == {
        "Source": "1",
        "Id": "78",
        "Name": "",
        "MACAddress": "58:a2:e1:03:96:f3",
        "LinkStatus": "LinkUp",
        "SpeedMbps": None,
        "IPv4": None,
        "Health": "OK",
    }
    assert rows_by_id["1"] == {
        "Source": "1",
        "Id": "1",
        "Name": "Manager Dedicated Network Interface",
        "MACAddress": "7C:A6:2A:40:C3:E4",
        "LinkStatus": "LinkUp",
        "SpeedMbps": 1000,
        "IPv4": "127.0.0.1",
        "Health": "OK",
    }
    assert rows_by_id["2"]["Name"] == "Manager Shared Network Interface"
    assert rows_by_id["2"]["MACAddress"] == "7C:A6:2A:40:C3:E5"
    assert rows_by_id["2"]["IPv4"] == "0.0.0.0"
    assert rows_by_id["2"]["Health"] is None
    assert rows_by_id["3"]["Name"] == "Manager Virtual Network Interface"
    assert rows_by_id["3"]["MACAddress"] == "0A:CA:FE:F0:0D:04"
    assert rows_by_id["3"]["IPv4"] is None
    assert rows_by_id["3"]["Health"] is None
    assert {
        request.method
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
