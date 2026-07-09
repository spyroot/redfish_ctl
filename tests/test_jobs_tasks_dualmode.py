"""Dual-mode tests for job and task read commands."""
import json

import pytest

import redfish_ctl.tasks.cmd_task_svc  # noqa: F401
from redfish_ctl.cmd_exceptions import AuthenticationFailed
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from tests.test_utils import create_json_resp

JOB_ID = "JID_000000000001"


def test_jobs_list_returns_expanded_job_members(redfish_api):
    """jobs_sources_query returns expanded Dell job resources offline."""
    result = redfish_api.sync_invoke(ApiRequestType.Jobs, "jobs_sources_query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert [job["Id"] for job in result.data] == [
        "JID_000000000002",
        JOB_ID,
    ]
    assert result.data[0]["JobState"] == "Running"


def test_jobs_list_can_return_only_job_ids(redfish_api):
    """jobs_sources_query can project the expanded job collection to IDs."""
    result = redfish_api.sync_invoke(
        ApiRequestType.Jobs,
        "jobs_sources_query",
        job_ids=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data == ["JID_000000000002", JOB_ID]


def test_job_get_returns_dell_oem_job(redfish_api):
    """job_query returns one Dell OEM job by job ID."""
    result = redfish_api.sync_invoke(
        ApiRequestType.JobGet,
        "job_query",
        job_id=JOB_ID,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        f"/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/{JOB_ID}"
    )
    assert result.data["Id"] == JOB_ID
    assert result.data["JobState"] == "Completed"


def test_job_del_deletes_existing_job_resource_in_mock_mode(
    redfish_mock, redfish_service
):
    """job_del sends DELETE to the Dell OEM job resource offline."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.JobDel,
        "job_del",
        job_id=JOB_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": "ok"}
    assert result.error is None
    assert redfish_service.last_request.method == "DELETE"
    assert redfish_service.last_request.path.lower() == (
        f"/redfish/v1/managers/idrac.embedded.1/oem/dell/jobs/{JOB_ID.lower()}"
    )
    assert redfish_service.last_request.text is None


def test_job_service_returns_service_capabilities(redfish_api):
    """job_service_query returns the Redfish JobService resource."""
    result = redfish_api.sync_invoke(ApiRequestType.JobServices, "job_service_query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == "/redfish/v1/JobService"
    assert result.data.data["ServiceCapabilities"]["MaxJobs"] == 256
    assert result.data.data["Jobs"]["@odata.id"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Jobs"
    )


def test_dell_job_service_returns_delete_queue_action(redfish_api):
    """job_service_query returns the Dell OEM JobService actions."""
    result = redfish_api.sync_invoke(
        ApiRequestType.JobDellServices,
        "job_service_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"
    )
    assert result.data.data["Id"] == "DellJobService"
    assert "DeleteJobQueue" in result.extra
    assert result.extra["DeleteJobQueue"].target == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService/"
        "Actions/DellJobService.DeleteJobQueue"
    )


def test_task_service_root_query_returns_task_links(redfish_api):
    """task_svc_query returns the TaskService root resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.ManagerQuery,
        "task_svc_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["Name"] == "Task Collection"
    assert result.data["Links"]["Members"][0]["@odata.id"] == (
        "/redfish/v1/TaskService/1"
    )
    assert result.discovered == {}


@pytest.mark.parametrize(
    ("do_force", "job_id"),
    [
        (False, "JID_CLEARALL"),
        (True, "JID_CLEARALL_FORCE"),
    ],
)
def test_job_delete_all_posts_clear_queue_payload_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch, do_force, job_id
):
    """job_delete_all POSTs the Dell queue-clear payload to the action target."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(IDracManager, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.JobRmDellServices,
        "job_delete_all",
        do_force=do_force,
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    assert redfish_service.last_request.method == "POST"
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/managers/idrac.embedded.1/oem/dell/delljobservice/"
        "actions/delljobservice.deletejobqueue"
    )
    assert redfish_service.last_request.json() == {"JobID": job_id}


def test_job_apply_posts_bios_job_creation_payload_in_mock_mode(
    redfish_mock, redfish_service
):
    """job_apply creates a BIOS apply job without rebooting offline."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.JobApply,
        "job_apply",
        setting="bios",
        do_watch=False,
    )

    post_requests = [
        request for request in redfish_service.requests if request.method == "POST"
    ]

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert len(post_requests) == 1
    post_path = post_requests[0].path.lower().replace("//", "/")
    assert post_path == "/redfish/v1/managers/idrac.embedded.1/jobs"

    payload = post_requests[0].json()
    assert payload["RebootJobType"] == "ForceReboot"
    assert payload["TargetSettingsURI"].endswith(
        "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"
    )
    assert payload["StartTime"] == "TIME_NOW"
    assert payload["EndTime"] == "TIME_NA"


def test_tasks_list_returns_task_collection_and_actions(redfish_api):
    """chassis_service_query for TasksList returns expanded task members."""
    result = redfish_api.sync_invoke(ApiRequestType.TasksList, "chassis_service_query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == "/redfish/v1/TaskService/Tasks"
    assert result.data.data["Members"][0]["Id"] == JOB_ID
    assert "Cancel" in result.discovered


def test_task_get_returns_one_task_resource(redfish_api):
    """task-get returns one TaskService task by task ID."""
    result = redfish_api.sync_invoke(
        ApiRequestType.TaskGet,
        "chassis_service_query",
        task_id=JOB_ID,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == f"/redfish/v1/TaskService/Tasks/{JOB_ID}"
    assert result.data.data["TaskState"] == "Completed"


def test_get_task_returns_completed_job_state_without_polling(redfish_api):
    """task_query returns the completed job state before polling TaskService."""
    result = redfish_api.sync_invoke(
        ApiRequestType.GetTask,
        "task_query",
        job_id=JOB_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.data.value == "Completed"


def test_fetch_task_raises_authentication_failed_on_task_poll_401(
    redfish_api, monkeypatch
):
    """fetch_task stops immediately when TaskService rejects polling auth."""
    task_get_urls = []

    def get_job(self, task_id):
        assert task_id == JOB_ID
        return {"JobState": "Running"}

    def api_get_call(self, url, hdr=None):
        assert url.endswith(f"/redfish/v1/TaskService/Tasks/{JOB_ID}")
        task_get_urls.append(url)
        return create_json_resp({"error": "unauthorized"}, status_code=401)

    monkeypatch.setattr(IDracManager, "get_job", get_job)
    monkeypatch.setattr(IDracManager, "api_get_call", api_get_call)

    with pytest.raises(AuthenticationFailed):
        redfish_api.fetch_task(JOB_ID, sleep_time=0)

    assert len(task_get_urls) == 1


def test_job_watch_returns_completed_job_state(redfish_api):
    """job_watch returns the terminal state for an existing Dell OEM job."""
    result = redfish_api.sync_invoke(
        ApiRequestType.JobWatch,
        "job_watch",
        job_id=JOB_ID,
    )

    assert isinstance(result, CommandResult)
    assert result.data.value == "Completed"
