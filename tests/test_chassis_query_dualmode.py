"""Dual-mode test for the chassis query command."""
import json

import pytest

from idrac_ctl.chassis.cmd_chasis_reset import ChassisReset
from idrac_ctl.chassis.cmd_update_chassis import ChassisUpdate  # noqa: F401
from idrac_ctl.cmd_exceptions import InvalidArgumentFormat
from idrac_ctl.idrac_shared import ApiRequestType, PowerState, RedfishAction
from idrac_ctl.redfish_manager import CommandResult


def test_chassis_query_returns_idrac_chassis_collection(redfish_api):
    """chassis_service_query returns the iDRAC chassis collection."""
    result = redfish_api.sync_invoke(
        ApiRequestType.ChassisQuery, "chassis_service_query"
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == "/redfish/v1/Chassis"
    assert result.data.data["Members"][0]["@odata.id"] == (
        "/redfish/v1/Chassis/System.Embedded.1"
    )


def test_power_state_property_returns_fixture_power_state(redfish_api):
    """power_state maps the chassis PowerState value onto the enum."""
    assert redfish_api.power_state is PowerState.On


def test_chassis_update_rejects_empty_id_before_patch(redfish_mock, redfish_service):
    """chassis-update rejects an empty chassis id before any PATCH is sent."""
    request_count = len(redfish_service.requests)

    with pytest.raises(InvalidArgumentFormat, match="chassis_id is empty string"):
        redfish_mock.sync_invoke(
            ApiRequestType.ChassisUpdate,
            "update_chassis",
            chassis_id="",
            from_spec="unused.json",
        )

    assert len(redfish_service.requests) == request_count


def test_chassis_reset_posts_forceoff_payload_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """reboot POSTs ForceOff to the discovered chassis reset action."""
    member_path = "/redfish/v1/Chassis/System.Embedded.1"
    member_response = redfish_mock.api_get_call(
        f"https://mock-idrac{member_path}", {}
    )
    assert member_response.status_code == 200, f"missing fixture for {member_path}"

    collection_response = redfish_mock.api_get_call(
        "https://mock-idrac/redfish/v1/Chassis", {}
    )
    assert collection_response.status_code == 200
    collection_path = redfish_service.last_request.path
    collection = collection_response.json()
    collection["Members"] = [member_response.json()]
    redfish_service._overlay[collection_path] = collection

    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(
        RedfishAction, "_args", property(lambda action: action.args), raising=False
    )
    monkeypatch.setattr(ChassisReset, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.ChassisReset,
        "reboot",
        reset_type="ForceOff",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "task_id": redfish_service.JOB_ID,
        "task_state": task_state,
    }
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None

    request = redfish_service.last_request
    expected_path = "/redfish/v1/Chassis/System.Embedded.1/Actions/Chassis.Reset"
    assert request.method == "POST"
    assert request.path.lower() == expected_path.lower()
    assert request.json() == {"ResetType": "ForceOff"}
