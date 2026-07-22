"""Dual-mode tests for boot-source update commands."""

import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

SETTINGS_PATH = "/redfish/v1/systems/system.embedded.1/oem/dell/dellbootsources/settings"


def test_boot_source_update_patches_pending_settings_in_mock_mode(
    redfish_mock, redfish_service, tmp_path
):
    """boot-source-update PATCHes a JSON spec with an on-reset apply time."""
    spec = tmp_path / "boot_source_update.json"
    payload = {
        "Attributes": {
            "UefiBootSeq": [
                {
                    "Enabled": True,
                    "Id": "BIOS.Setup.1-1#UefiBootSeq#RAID.Integrated.1-1",
                    "Index": 0,
                    "Name": "RAID.Integrated.1-1",
                },
                {
                    "Enabled": True,
                    "Id": "BIOS.Setup.1-1#UefiBootSeq#NIC.PxeDevice.1-1",
                    "Index": 1,
                    "Name": "NIC.PxeDevice.1-1",
                },
            ]
        }
    }
    spec.write_text(json.dumps(payload))

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootSourceUpdate,
        "update",
        apply="on-reset",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == SETTINGS_PATH
    assert request.json() == {
        **payload,
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }

    current = redfish_mock.sync_invoke(
        ApiRequestType.BootSourcePending,
        "query_pending",
    )
    assert current.data["UefiBootSeq"][0]["Name"] == "RAID.Integrated.1-1"
