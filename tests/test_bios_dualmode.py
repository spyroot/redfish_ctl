"""Dual-mode tests for BIOS pending and registry commands."""
import json

from redfish_ctl.idrac_shared import ApiRequestType, JobState
from redfish_ctl.redfish_manager import CommandResult


def test_bios_pending_returns_pending_attributes(redfish_api):
    """bios_query_pending unwraps Attributes from the pending BIOS settings resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosQueryPending,
        "bios_query_pending",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["BootMode"] == "Uefi"
    assert result.data["SriovGlobalEnable"] == "Enabled"


def test_bios_pending_filter_returns_single_attribute_value(redfish_api):
    """bios_query_pending returns the selected pending BIOS attribute value."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosQueryPending,
        "bios_query_pending",
        data_filter="SriovGlobalEnable",
    )

    assert isinstance(result, CommandResult)
    assert result.data == "Enabled"


def test_bios_registry_returns_registry_entries(redfish_api):
    """bios_registry returns BIOS registry attributes."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosRegistry,
        "bios_registry",
        is_registry_only=True,
        is_value_only=False,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert result.data[0]["AttributeName"] == "ProcCStates"
    assert result.data[0]["ReadOnly"] is False


def test_bios_registry_filters_attribute_names(redfish_api):
    """bios_registry filters registry entries by requested attribute name."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosRegistry,
        "bios_registry",
        attr_name="SriovGlobalEnable",
        is_value_only=False,
    )

    assert isinstance(result, CommandResult)
    assert len(result.data) == 1
    assert result.data[0]["AttributeName"] == "SriovGlobalEnable"
    assert result.data[0]["Value"] == [
        {"ValueName": "Enabled"},
        {"ValueName": "Disabled"},
    ]


def test_bios_registry_noreadonly_filters_read_only_entries(redfish_api):
    """bios_registry --noreadonly removes read-only registry attributes."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosRegistry,
        "bios_registry",
        no_read_only=True,
        is_value_only=False,
    )

    assert isinstance(result, CommandResult)
    assert [entry["AttributeName"] for entry in result.data] == [
        "ProcCStates",
        "SriovGlobalEnable",
    ]
    assert all(entry["ReadOnly"] is False for entry in result.data)


def test_bios_change_settings_patches_pending_settings_in_mock_mode(
    redfish_mock, redfish_service, tmp_path
):
    """bios-change PATCHes a JSON spec with an on-reset apply time."""
    settings_path = "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"
    redfish_service._overlay[settings_path.lower()] = {"Attributes": {}}
    payload = {
        "Attributes": {
            "ProcCStates": "Disabled",
            "SriovGlobalEnable": "Enabled",
        }
    }
    spec = tmp_path / "bios_change.json"
    spec.write_text(json.dumps(payload))

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosChangeSettings,
        "bios_change_settings",
        apply="on-reset",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == settings_path.lower()
    assert request.json() == {
        **payload,
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }


def test_bios_clear_pending_posts_discovered_action_in_mock_mode(
    redfish_mock, redfish_service
):
    """bios-clear-pending POSTs the discovered ClearPending action."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosClearPending,
        "clear_pending",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == JobState.Completed

    post_requests = [
        request for request in redfish_service.requests if request.method == "POST"
    ]
    assert len(post_requests) == 1
    request = post_requests[0]
    assert request.path.lower() == (
        "/redfish/v1/Systems/System.Embedded.1/Bios/Settings/Actions/"
        "Bios.ClearPending"
    ).lower()
    assert request.json() == {}
