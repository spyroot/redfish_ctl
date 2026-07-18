"""Dual-mode-style coverage for DellTimeService.ManageTime."""

import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.manager.cmd_dell_time_manage import DellTimeManage
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_TIME_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellTimeService"
)
MANAGE_TARGET = f"{DELL_TIME_SERVICE}/Actions/DellTimeService.ManageTime"
QUERY_TIME = "2023-06-02T11:03:14-05:00"
SET_TIME = "2026-07-18T03:00:00+00:00"


@pytest.fixture
def dell_time_manager():
    """Serve the committed Dell corpus with a ManageTime POST response."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )

    def post_cb(request, context):
        if request.path.lower() == MANAGE_TARGET.lower():
            service.requests.append(request)
            payload = request.json() if request.text else {}
            context.status_code = 200
            if payload.get("GetRequest") is True:
                return json.dumps({"TimeData": QUERY_TIME})
            return json.dumps({"TimeData": payload.get("TimeData")})
        return service.post_cb(request, context)

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-dell-time",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def test_dell_time_manage_queries_time_by_default(dell_time_manager):
    """The default query sends the read-style ManageTime payload."""
    manager, service = dell_time_manager

    result = manager.sync_invoke(
        ApiRequestType.DellTimeManage,
        "dell-time-manage",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "#DellTimeService.ManageTime"
    assert result.data["payload"] == {"GetRequest": True}
    assert result.data["level"] == "read_only"
    assert result.data["executed"] is True
    assert result.data["results"][0]["response"] == {"Status": "ok"}
    assert len(posts) == 1
    assert posts[0].path.lower() == MANAGE_TARGET.lower()
    assert posts[0].json() == {"GetRequest": True}


def test_dell_time_manage_dry_run_suppresses_query_post(dell_time_manager):
    """--dry_run resolves the Dell target without POSTing."""
    manager, service = dell_time_manager

    result = manager.sync_invoke(
        ApiRequestType.DellTimeManage,
        "dell-time-manage",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellTimeService.ManageTime"
    assert result.data["payload"] == {"GetRequest": True}
    assert result.data["level"] == "read_only"
    assert result.data["blocked"] is None
    assert result.data["targets"] == [{
        "Manager": "iDRAC.Embedded.1",
        "ManagerUri": "/redfish/v1/Managers/iDRAC.Embedded.1",
        "Service": DELL_TIME_SERVICE,
        "Target": MANAGE_TARGET,
    }]
    assert _post_requests(service) == []


def test_dell_time_manage_set_requires_confirm(dell_time_manager):
    """A set-time payload is previewed unless --confirm is present."""
    manager, service = dell_time_manager

    result = manager.sync_invoke(
        ApiRequestType.DellTimeManage,
        "dell-time-manage",
        set_time=SET_TIME,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "destructive"
    assert result.data["payload"] == {
        "GetRequest": False,
        "TimeData": SET_TIME,
    }
    assert result.data["blocked"] == (
        "DellTimeService.ManageTime set requires --confirm"
    )
    assert _post_requests(service) == []


def test_dell_time_manage_confirm_posts_set_payload(dell_time_manager):
    """--confirm sends exactly one set-time POST to the discovered Dell target."""
    manager, service = dell_time_manager

    result = manager.sync_invoke(
        ApiRequestType.DellTimeManage,
        "dell-time-manage",
        set_time=SET_TIME,
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert result.data["results"][0]["response"] == {"Status": "ok"}
    assert len(posts) == 1
    assert posts[0].path.lower() == MANAGE_TARGET.lower()
    assert posts[0].json() == {
        "GetRequest": False,
        "TimeData": SET_TIME,
    }


def test_dell_time_manage_reports_missing_action_without_post(redfish_mock_factory):
    """A fixture without DellTimeService reports an error and never POSTs."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellTimeManage,
        "dell-time-manage",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "action": "#DellTimeService.ManageTime",
        "available": [],
    }
    assert result.error == "DellTimeService.ManageTime action not found"
    assert _post_requests(service) == []


def test_dell_time_manage_rejects_invalid_set_time(dell_time_manager):
    """Invalid set-time values fail before POST."""
    manager, service = dell_time_manager

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.DellTimeManage,
            "dell-time-manage",
            set_time="not-a-date",
            confirm=True,
        )

    assert _post_requests(service) == []


def test_dell_time_manage_is_registered():
    """The dell-time-manage command is wired into the command registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellTimeManage]["dell-time-manage"] is (
        DellTimeManage
    )

    cmd_parser, cmd_name, cmd_help = DellTimeManage.register_subcommand(
        DellTimeManage
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-time-manage"
    assert "Dell service-processor time" in cmd_help
    assert "--set-time" in help_text
    assert "--confirm" in help_text
    assert "--dry_run" in help_text


def test_dell_time_manage_policy_is_destructive():
    """Generic action listings classify ManageTime as guarded by default."""
    assert classify("#DellTimeService.ManageTime") is Destructiveness.DESTRUCTIVE
