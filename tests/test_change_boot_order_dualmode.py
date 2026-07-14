"""Dual-mode tests for the change-boot-order command."""

import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

SYSTEM_PATH = "/redfish/v1/systems/system.embedded.1"


def test_change_boot_order_patches_boot_order_from_spec(
    redfish_mock, redfish_service, tmp_path
):
    """change-boot-order PATCHes the ComputerSystem BootOrder from a JSON spec."""
    payload = {
        "Boot": {
            "BootOrder": [
                "NIC.PxeDevice.1-1",
                "HardDisk.List.1-1",
            ]
        }
    }
    spec = tmp_path / "change_boot_order.json"
    spec.write_text(json.dumps(payload))

    result = redfish_mock.sync_invoke(
        ApiRequestType.ChangeBootOrder,
        "change_boot_order",
        boot_order="",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    assert result.error is None

    requests = [request for request in redfish_service.requests if request.method == "PATCH"]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert request.path.lower() == SYSTEM_PATH
    assert request.json() == payload
