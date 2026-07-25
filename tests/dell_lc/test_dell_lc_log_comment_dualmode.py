"""Dual-mode-style coverage for DellLCService.InsertCommentInLCLog."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.dell_lc.cmd_dell_lc_log_comment import DellLcLogComment
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
LC_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
INSERT_TARGET = f"{LC_SERVICE}/Actions/DellLCService.InsertCommentInLCLog"
INSERT_ACTION = "#DellLCService.InsertCommentInLCLog"


@pytest.fixture
def dell_lc_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    The vendor-faithful service realizes an Action POST the Dell way: 202 plus
    a ``JID_`` OEM job id in the Location header, never a DMTF-generic token.

    :return: tuple of IDracManager and the recording MockRedfishService.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-lc",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: the recording MockRedfishService.
    :return: list of POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_lc_log_comment_lists_target_without_mutating(dell_lc_mock):
    """Without a comment, the command lists the target and never POSTs."""
    manager, service = dell_lc_mock

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
    assert _post_requests(service) == []


def test_lc_log_comment_without_confirm_is_preview_only(dell_lc_mock):
    """InsertCommentInLCLog resolves the target but does not POST by default."""
    manager, service = dell_lc_mock

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
    assert _post_requests(service) == []


def test_lc_log_comment_confirm_posts_payload(dell_lc_mock):
    """--confirm POSTs the comment; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcLogComment,
        "dell-lc-log-comment",
        comment="Investigated PSU event",
        log_sequence_number="42",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == INSERT_ACTION
    assert result.data["target"] == INSERT_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == INSERT_TARGET.lower()
    assert posts[0].json() == {
        "Comment": "Investigated PSU event",
        "LogSequenceNumber": "42",
    }


def test_lc_log_comment_confirm_dry_run_still_does_not_post(dell_lc_mock):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_lc_mock

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
    assert _post_requests(service) == []


def test_lc_log_comment_rejects_empty_comment(dell_lc_mock):
    """A blank comment is rejected before any action POST can fire."""
    manager, service = dell_lc_mock

    with pytest.raises(InvalidArgument, match="comment cannot be empty"):
        manager.sync_invoke(
            ApiRequestType.DellLcLogComment,
            "dell-lc-log-comment",
            comment="   ",
            confirm=True,
        )

    assert _post_requests(service) == []


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
