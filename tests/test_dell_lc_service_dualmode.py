"""Dual-mode test for the Dell Lifecycle Controller service query command."""
import json

import pytest

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_dell_lc_service_query_returns_actions(redfish_api):
    """dell_lc_services returns the Dell LC service resource and its actions."""
    result = redfish_api.sync_invoke(ApiRequestType.DellLcQuery, "dell_lc_services")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == (
        "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService"
    )
    assert result.data.data["ServiceEnabled"] is True
    assert "GetRemoteServicesAPIStatus" in result.discovered
    assert "GetRSStatus" in result.discovered


@pytest.mark.parametrize(
    ("request_type", "command_name", "action_name"),
    [
        (
            ApiRequestType.RemoteServicesAPIStatus,
            "dell_lc_status",
            "getremoteservicesapistatus",
        ),
        (
            ApiRequestType.RemoteServicesRssAPIStatus,
            "dell_lc_rs_status",
            "getrsstatus",
        ),
    ],
)
def test_dell_lc_status_commands_post_empty_payload_to_action(
    redfish_mock,
    redfish_service,
    monkeypatch,
    request_type,
    command_name,
    action_name,
):
    """Dell LC status commands POST an empty body and report the generated task."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(CommandBase, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(request_type, command_name)

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/managers/idrac.embedded.1/delllcservice/"
        f"actions/delllcservice.{action_name}"
    )
    assert request.json() == {}
