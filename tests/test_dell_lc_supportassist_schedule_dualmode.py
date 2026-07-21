"""Dual-mode-style coverage for DellLCService SupportAssist schedules."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_supportassist_schedule import (
    DellLcSupportAssistSchedule,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
DELL_LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
CLEAR_TARGET = (
    f"{DELL_LC_SERVICE}/Actions/"
    "DellLCService.SupportAssistClearAutoCollectSchedule"
)
SET_TARGET = (
    f"{DELL_LC_SERVICE}/Actions/"
    "DellLCService.SupportAssistSetAutoCollectSchedule"
)


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_lc_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of RedfishManagerBase and recorded requests list.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/supportassist-1"
        return json.dumps(
            {"Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/supportassist-1"}}
        )

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-lc-supportassist",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_lc_supportassist_schedule_policy_is_reversible():
    """The SupportAssist schedule actions are classified as reversible."""
    assert (
        classify("#DellLCService.SupportAssistClearAutoCollectSchedule")
        is Destructiveness.REVERSIBLE
    )
    assert (
        classify("#DellLCService.SupportAssistSetAutoCollectSchedule")
        is Destructiveness.REVERSIBLE
    )


def test_dell_lc_supportassist_schedule_lists_targets_without_post(
    dell_lc_manager,
):
    """Listing discovers schedule actions and does not POST."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["supportassist_schedule_targets"] == [
        {
            "Resource": DELL_LC_SERVICE,
            "Selector": "clear",
            "Action": "#DellLCService.SupportAssistClearAutoCollectSchedule",
            "Target": CLEAR_TARGET,
        },
        {
            "Resource": DELL_LC_SERVICE,
            "Selector": "set",
            "Action": "#DellLCService.SupportAssistSetAutoCollectSchedule",
            "Target": SET_TARGET,
            "AllowedRecurrences": ["Monthly", "Quarterly", "Weekly"],
        },
    ]
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_set_previews_by_default(
    dell_lc_manager,
):
    """Setting a recurrence does not POST unless --confirm is present."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="set",
        recurrence="Weekly",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.SupportAssistSetAutoCollectSchedule"
    assert result.data["target"] == SET_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"Recurrence": "Weekly"}
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_set_confirm_posts_payload(
    dell_lc_manager,
):
    """--confirm POSTs the recurrence payload to the set action target."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="set",
        recurrence="Weekly",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.SupportAssistSetAutoCollectSchedule"
    assert result.data["target"] == SET_TARGET
    assert result.data["level"] == "reversible"
    assert result.data["task_id"] == "supportassist-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == SET_TARGET.lower()
    assert posts[0].json() == {"Recurrence": "Weekly"}


def test_dell_lc_supportassist_schedule_clear_previews_by_default(
    dell_lc_manager,
):
    """Clearing the schedule resolves the target but does not POST by default."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="clear",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellLCService.SupportAssistClearAutoCollectSchedule"
    assert result.data["target"] == CLEAR_TARGET
    assert result.data["payload"] == {}
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_clear_confirm_posts_empty_payload(
    dell_lc_manager,
):
    """--confirm POSTs an empty payload to the clear action target."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="clear",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == CLEAR_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == CLEAR_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_lc_supportassist_schedule_dry_run_overrides_confirm(
    dell_lc_manager,
):
    """--dry_run keeps the command preview-only even with --confirm."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="set",
        recurrence="Monthly",
        dry_run=True,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"Recurrence": "Monthly"}
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_rejects_invalid_recurrence(
    dell_lc_manager,
):
    """Inline allowable values reject unsupported recurrences before POST."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="set",
        recurrence="Yearly",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.SupportAssistSetAutoCollectSchedule "
        "Recurrence: Yearly; allowed: Monthly, Quarterly, Weekly"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "Recurrence",
            "value": "Yearly",
            "allowed": ["Monthly", "Quarterly", "Weekly"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_set_requires_recurrence(
    dell_lc_manager,
):
    """The set action fails closed when Recurrence is omitted."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="set",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "SupportAssist schedule set requires --recurrence"
    assert result.data == {
        "required": ["Recurrence"],
        "action": "#DellLCService.SupportAssistSetAutoCollectSchedule",
    }
    assert _post_requests(requests) == []


def test_dell_lc_supportassist_schedule_missing_action_does_not_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports no schedule target and no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistSchedule,
        "dell-lc-supportassist-schedule",
        action="clear",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell LC SupportAssist schedule action not found"
    assert result.data == {
        "action": "#DellLCService.SupportAssistClearAutoCollectSchedule",
        "available": [],
    }
    assert _post_requests(service.requests) == []


def test_dell_lc_supportassist_schedule_exposes_cli_entrypoint():
    """The command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert (
        registry[ApiRequestType.DellLcSupportAssistSchedule][
            "dell-lc-supportassist-schedule"
        ]
        is DellLcSupportAssistSchedule
    )

    cmd_parser, cmd_name, cmd_help = (
        DellLcSupportAssistSchedule.register_subcommand(
            DellLcSupportAssistSchedule
        )
    )

    assert "--recurrence" in cmd_parser.format_help()
    assert cmd_name == "dell-lc-supportassist-schedule"
    assert "SupportAssist schedule" in cmd_help
