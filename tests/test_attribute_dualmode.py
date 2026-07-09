"""Dual-mode tests for iDRAC manager attribute commands."""
import json

from redfish_ctl.attribute.cmd_attribute_clear_pending import AttributeClearPending
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_attribute_inventory_returns_manager_attributes(redfish_api):
    """attribute_inventory returns the manager attribute resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.AttributesQuery,
        "attribute_inventory",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"].endswith("/Attributes")
    assert "AttributeRegistry" in result.data
    assert isinstance(result.data["Attributes"], dict)
    assert "SystemInfo.1.BootTime" in result.data["Attributes"]


def test_attribute_inventory_filters_single_attribute(redfish_api):
    """attr_filter narrows the manager attribute payload to matching keys."""
    result = redfish_api.sync_invoke(
        ApiRequestType.AttributesQuery,
        "attribute_inventory",
        attr_filter="SystemInfo.1.BootTime",
    )

    assert isinstance(result, CommandResult)
    assert set(result.data) == {"SystemInfo.1.BootTime"}
    assert isinstance(result.data["SystemInfo.1.BootTime"], str)


def test_attribute_inventory_filters_attribute_family(redfish_api):
    """attr_filter can return a family of matching manager attribute keys."""
    result = redfish_api.sync_invoke(
        ApiRequestType.AttributesQuery,
        "attribute_inventory",
        attr_filter="ServerTopology",
    )

    assert isinstance(result, CommandResult)
    assert result.data
    assert all("ServerTopology" in key for key in result.data)
    assert "Attributes" not in result.data
    assert "AttributeRegistry" not in result.data
    assert "@odata.id" not in result.data


def test_attribute_inventory_attr_only_returns_plain_attributes(redfish_api):
    """attr_only unwraps the Attributes object from the manager resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.AttributesQuery,
        "attribute_inventory",
        attr_only=True,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert "@odata.id" not in result.data
    assert "AttributeRegistry" not in result.data
    assert "SystemInfo.1.BootTime" in result.data


def test_attribute_update_patches_manager_attributes_in_mock_mode(
    redfish_mock, redfish_service, tmp_path
):
    """attribute_update PATCHes the manager attribute payload from a JSON spec."""
    spec = tmp_path / "attribute_update.json"
    payload = {"Attributes": {"OwnerInfo.1.OwnerName": "idrac_ctl"}}
    spec.write_text(json.dumps(payload))

    result = redfish_mock.sync_invoke(
        ApiRequestType.AttributesUpdate,
        "attribute_update",
        from_spec=str(spec),
    )

    assert isinstance(result, CommandResult)
    assert result.data["Status"] == "ok"
    request = redfish_service.last_request
    assert request.method == "PATCH"
    assert request.path.lower() == "/redfish/v1/managers/system.embedded.1/attributes"
    assert request.json() == payload

    current = redfish_mock.sync_invoke(
        ApiRequestType.AttributesQuery,
        "attribute_inventory",
        attr_filter="OwnerInfo.1.OwnerName",
    )
    assert current.data == {"OwnerInfo.1.OwnerName": "idrac_ctl"}


def test_attribute_clear_pending_posts_discovered_action_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """attribute_clear_pending POSTs the discovered clear-pending action."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(AttributeClearPending, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.AttributeClearPending,
        "clear_pending",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/managers/system.embedded.1/attributes/"
        "actions/dellmanager.clearpending"
    )
    assert request.json() == {}
