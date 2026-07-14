"""Dual-mode tests for the BIOS snapshot command."""
import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_bios_snapshot_returns_json_restore_spec(redfish_api):
    """bios_snapshot returns the current BIOS attributes as a change spec."""
    result = redfish_api.sync_invoke(ApiRequestType.BiosSnapshot, "bios_snapshot")

    assert isinstance(result, CommandResult)
    assert set(result.data.keys()) == {"Attributes"}
    assert isinstance(result.data["Attributes"], dict)
    json.dumps(result.data)
    assert result.data["Attributes"]
    if "BootMode" in result.data["Attributes"]:
        assert result.data["Attributes"]["BootMode"] in {"Uefi", "Bios"}


def test_bios_snapshot_attr_name_scopes_restore_spec(redfish_mock, redfish_service):
    """attr_name snapshots only requested current BIOS attribute values."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosSnapshot,
        "bios_snapshot",
        attr_name="ProcCStates, MissingAttribute",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Attributes": {"ProcCStates": "Disabled"}}
    assert not [
        request
        for request in redfish_service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def test_bios_snapshot_from_spec_scopes_restore_spec(
    redfish_mock, redfish_service, tmp_path
):
    """from_spec snapshots current values for attributes named in a change spec."""
    spec = tmp_path / "bios-change.json"
    spec.write_text(
        json.dumps(
            {
                "Attributes": {
                    "BootMode": "Bios",
                    "SriovGlobalEnable": "Disabled",
                    "MissingAttribute": "Ignored",
                }
            }
        ),
        encoding="utf-8",
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosSnapshot,
        "bios_snapshot",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "Attributes": {
            "BootMode": "Uefi",
            "SriovGlobalEnable": "Enabled",
        }
    }
    json.dumps(result.data)
    assert not [
        request
        for request in redfish_service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]
