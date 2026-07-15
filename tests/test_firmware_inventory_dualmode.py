"""Dual-mode test for the firmware inventory command."""
import json
from urllib.parse import unquote, urlsplit

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

FIRMWARE_INVENTORY_PATH = "/redfish/v1/UpdateService/FirmwareInventory"


def test_firmware_inventory_returns_inventory_collection(redfish_api):
    """firmware_inv_query returns the firmware inventory collection."""
    result = redfish_api.sync_invoke(
        ApiRequestType.FirmwareInventoryQuery, "firmware_inv_query"
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == FIRMWARE_INVENTORY_PATH
    assert result.data["Members"][0]["Id"] == "BIOS"


def test_firmware_inventory_expanded_get_saves_collection_in_mock_mode(
    redfish_mock,
    redfish_service,
    tmp_path,
):
    """firmware_inv_query always GETs FirmwareInventory with Redfish expand."""
    output_file = tmp_path / "firmware_inventory"

    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareInventoryQuery,
        "firmware_inv_query",
        filename=str(output_file),
    )

    assert isinstance(result, CommandResult)
    assert result.data["@odata.id"] == FIRMWARE_INVENTORY_PATH
    assert result.data["Members"][0]["Id"] == "BIOS"

    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower() == FIRMWARE_INVENTORY_PATH.lower()

    raw_query = request.query or urlsplit(request.url).query
    assert unquote(raw_query) == "$expand=*($levels=1)"
    assert all(
        recorded.method not in {"POST", "PATCH", "DELETE"}
        for recorded in redfish_service.requests
    )

    saved_path = output_file.with_suffix(".json")
    assert saved_path.exists()
    assert json.loads(saved_path.read_text()) == result.data
