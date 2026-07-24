"""Dual-mode-style coverage for DellLCService.InsertCommentInLCLog."""
import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.dell_lc.cmd_dell_lc_log_comment import DellLcLogComment
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
LC_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
INSERT_TARGET = f"{LC_SERVICE}/Actions/DellLCService.InsertCommentInLCLog"
INSERT_ACTION = "#DellLCService.InsertCommentInLCLog"


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

    :return: tuple of IDracManager and recorded requests list.
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
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/lclog-comment-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/lclog-comment-1"}
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-lc",
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


def test_lc_log_comment_lists_target_without_mutating(dell_lc_manager):
    """Without a comment, the command lists the target and never POSTs."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "lc_service": LC_SERVICE,
        "action": INSERT_ACTION,
        "target": INSERT_TARGET,
    }
    assert _post_requests(requests) == []


def test_lc_log_comment_without_confirm_is_preview_only(dell_lc_manager):
    """InsertCommentInLCLog resolves the target but does not POST by default."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
        comment=" maintenance note ",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == INSERT_ACTION
    assert result.data["target"] == INSERT_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {"Comment": "maintenance note"}
    assert _post_requests(requests) == []


def test_lc_log_comment_confirm_posts_payload(dell_lc_manager):
    """--confirm POSTs the comment payload to the discovered action target."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
        comment="Investigated PSU event",
        log_sequence_number="42",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == INSERT_ACTION
    assert result.data["target"] == INSERT_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "lclog-comment-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == INSERT_TARGET.lower()
    assert posts[0].json() == {
        "Comment": "Investigated PSU event",
        "LogSequenceNumber": "42",
    }


def test_lc_log_comment_confirm_dry_run_still_does_not_post(dell_lc_manager):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, requests = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
        comment="do not send",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["payload"] == {"Comment": "do not send"}
    assert _post_requests(requests) == []


def test_lc_log_comment_rejects_empty_comment(dell_lc_manager):
    """A blank comment is rejected before any action POST can fire."""
    manager, requests = dell_lc_manager

    with pytest.raises(InvalidArgument, match="comment cannot be empty"):
        manager.sync_invoke(
            ApiRequestType.DellLcLogComment,
            "dell-lc-log-comment",
            comment="   ",
            confirm=True,
        )

    assert _post_requests(requests) == []


def test_lc_log_comment_reports_missing_action_without_post(redfish_mock):
    """Older Dell fixtures without InsertCommentInLCLog return a target error."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
        comment="maintenance note",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#DellLCService.InsertCommentInLCLog' not found on "
        "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService"
    )
    assert result.data["action"] == INSERT_ACTION
    assert "GetRSStatus" in result.data["available"]


def test_lc_log_comment_exposes_cli_entrypoint():
    """The dell-lc-log-comment command is wired into the package registry."""
    registry = IDracManager._registry
    assert registry[ApiRequestType.DellLcLogComment]["dell-lc-log-comment"] is (
        DellLcLogComment
    )

    cmd_parser, cmd_name, cmd_help = DellLcLogComment.register_subcommand(
        DellLcLogComment
    )

    assert cmd_name == "dell-lc-log-comment"
    assert "comment" in cmd_help
    assert any(action.dest == "comment" for action in cmd_parser._actions)
