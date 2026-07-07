"""Dual-mode tests for BIOS snapshot and show-only BIOS changes."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def _write_bios_spec(tmp_path, attributes):
    """Write a minimal bios-change spec and return its path."""
    spec = tmp_path / "bios_change.json"
    spec.write_text(json.dumps({"Attributes": attributes}))
    return spec


def test_bios_snapshot_returns_current_bios_restore_spec(redfish_api):
    """bios_snapshot returns current BIOS attributes as a rollback spec."""
    result = redfish_api.sync_invoke(ApiRequestType.BiosSnapshot, "bios_snapshot")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["Attributes"]["ProcCStates"] == "Disabled"
    assert result.data["Attributes"]["SriovGlobalEnable"] == "Enabled"


def test_bios_snapshot_from_spec_returns_only_named_current_values(redfish_api, tmp_path):
    """bios_snapshot --from_spec scopes rollback output to changed keys."""
    spec = _write_bios_spec(
        tmp_path,
        {
            "ProcCStates": "Enabled",
            "SriovGlobalEnable": "Disabled",
        },
    )

    result = redfish_api.sync_invoke(
        ApiRequestType.BiosSnapshot,
        "bios_snapshot",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "Attributes": {
            "ProcCStates": "Disabled",
            "SriovGlobalEnable": "Enabled",
        }
    }


def test_bios_change_show_builds_payload_without_mutating_mock(
    redfish_mock, redfish_service, tmp_path
):
    """bios_change_settings do_show returns the pending payload without writes."""
    spec = _write_bios_spec(tmp_path, {"ProcCStates": "Enabled"})

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosChangeSettings,
        "bios_change_settings",
        from_spec=str(spec),
        apply="on-reset",
        do_show=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "Attributes": {"ProcCStates": "Enabled"},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }
    assert [
        request
        for request in redfish_service.requests
        if request.method in {"PATCH", "POST", "DELETE"}
    ] == []
