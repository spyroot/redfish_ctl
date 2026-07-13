"""Request-count benchmark harness for hot read paths."""

from redfish_ctl.command_shared import ApiRequestType
from tests.request_benchmark import assert_read_budget, recorded_requests

FIRMWARE_INVENTORY_PATH = "/redfish/v1/UpdateService/FirmwareInventory"


def test_firmware_inventory_read_budget(redfish_mock, redfish_service):
    """Firmware inventory should remain a single expanded GET."""
    result = assert_read_budget(
        redfish_mock,
        redfish_service,
        api_call=ApiRequestType.FirmwareInventoryQuery,
        name="firmware_inv_query",
        max_requests=1,
        max_india_vpn_seconds=0.301,
    )

    firmware_gets = recorded_requests(
        redfish_service,
        method="GET",
        path=FIRMWARE_INVENTORY_PATH,
    )

    assert result.data["@odata.id"] == FIRMWARE_INVENTORY_PATH
    assert len(firmware_gets) == 1
    assert len(redfish_service.requests) == 1
