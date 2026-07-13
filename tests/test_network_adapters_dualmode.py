"""Dual-mode tests for the network-adapters command."""

import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

EXPECTED_NETWORK_ADAPTER_ROWS = [
    {
        "Chassis": "NetworkFabric.1",
        "Id": "NIC.Integrated.1-1",
        "Model": "ConnectX-7 25GbE Adapter",
        "Manufacturer": "NVIDIA",
        "DeviceClass": "NIC",
        "SerialNumber": "NIC-MOCK-SN001",
        "PartNumber": "900-9X7AE-MOCK",
        "Health": "OK",
    },
    {
        "Chassis": "NetworkFabric.1",
        "Id": "DPU.Slot.2-1",
        "Model": "BlueField-3 DPU",
        "Manufacturer": "NVIDIA",
        "DeviceClass": "DPU",
        "SerialNumber": "DPU-MOCK-SN001",
        "PartNumber": "900-9D3B6-MOCK",
        "Health": "Warning",
    },
]


def test_network_adapters_returns_idrac_nic_and_dpu_rows(redfish_api):
    """network-adapters walks Dell chassis links and classifies NIC/DPU rows."""
    result = redfish_api.sync_invoke(
        ApiRequestType.NetworkAdapters,
        "network-adapters",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert result.data == EXPECTED_NETWORK_ADAPTER_ROWS
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None


def test_network_adapters_skips_chassis_with_missing_adapter_collection(
    redfish_mock,
    redfish_service,
):
    """network-adapters skips bad optional adapter links without mutating."""
    collection_path = "/redfish/v1/Chassis"
    collection_response = redfish_mock.api_get_call(
        f"https://mock-idrac{collection_path}", {}
    )
    collection = collection_response.json()
    collection["Members"] = [
        *collection["Members"],
        {"@odata.id": "/redfish/v1/Chassis/BrokenAdapters.1"},
    ]
    collection["Members@odata.count"] = len(collection["Members"])
    redfish_service._overlay[collection_path.lower()] = collection
    redfish_service._overlay["/redfish/v1/chassis/brokenadapters.1"] = {
        "@odata.id": "/redfish/v1/Chassis/BrokenAdapters.1",
        "Id": "BrokenAdapters.1",
        "NetworkAdapters": {
            "@odata.id": "/redfish/v1/Chassis/BrokenAdapters.1/NetworkAdapters"
        },
    }

    result = redfish_mock.sync_invoke(
        ApiRequestType.NetworkAdapters,
        "network-adapters",
    )

    assert isinstance(result, CommandResult)
    assert result.data == EXPECTED_NETWORK_ADAPTER_ROWS
    assert result.error is None
    assert [
        request.method
        for request in redfish_service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ] == []
