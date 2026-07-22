"""Dual-mode-style tests for Dell LC SupportAssist status actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_supportassist_status import (
    DellLcSupportAssistStatus,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
EULA_TARGET = (
    f"{DELL_LC_SERVICE}/Actions/DellLCService.SupportAssistGetEULAStatus"
)
SCHEDULE_TARGET = (
    f"{DELL_LC_SERVICE}/Actions/"
    "DellLCService.SupportAssistGetAutoCollectSchedule"
)


@pytest.fixture
def dell_lc_corpus_mock():
    """Return a manager and mock service backed by the full Dell corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-lc-supportassist",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_supportassist_status_policy_is_read_only():
    """The two Dell LC status actions are classified as read-only POSTs."""
    assert (
        classify("#DellLCService.SupportAssistGetEULAStatus")
        is Destructiveness.READ_ONLY
    )
    assert (
        classify("#DellLCService.SupportAssistGetAutoCollectSchedule")
        is Destructiveness.READ_ONLY
    )


def test_dell_lc_supportassist_status_lists_targets_without_post(
    dell_lc_corpus_mock,
):
    """Listing discovers the status actions and never POSTs."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistStatus,
        "dell-lc-supportassist-status",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {"auto-collect-schedule", "eula-status"}
    assert actions["auto-collect-schedule"]["Target"] == SCHEDULE_TARGET
    assert actions["eula-status"]["Target"] == EULA_TARGET
    assert actions["eula-status"]["Resource"] == DELL_LC_SERVICE
    assert _post_requests(service) == []


def test_dell_lc_supportassist_status_posts_selected_target(
    dell_lc_corpus_mock,
):
    """A selected status action POSTs an empty payload to the discovered target."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistStatus,
        "dell-lc-supportassist-status",
        action="eula-status",
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.SupportAssistGetEULAStatus"
    assert result.data["target"] == EULA_TARGET
    assert result.data["level"] == "read_only"
    assert len(posts) == 1
    assert posts[0].path.lower() == EULA_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_lc_supportassist_status_dry_run_does_not_post(
    dell_lc_corpus_mock,
):
    """--dry_run resolves the status action target without sending a POST."""
    manager, service = dell_lc_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistStatus,
        "dell-lc-supportassist-status",
        action="auto-collect-schedule",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == (
        "#DellLCService.SupportAssistGetAutoCollectSchedule"
    )
    assert result.data["target"] == SCHEDULE_TARGET
    assert result.data["level"] == "read_only"
    assert result.data["blocked"] is None
    assert result.data["payload"] == {}
    assert _post_requests(service) == []


def test_dell_lc_supportassist_status_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports no SupportAssist status target and no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellLcSupportAssistStatus,
        "dell-lc-supportassist-status",
        action="eula-status",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "Dell LC SupportAssist status action not found: eula-status"
    )
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_dell_lc_supportassist_status_exposes_cli_entrypoint():
    """The command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert (
        registry[ApiRequestType.DellLcSupportAssistStatus][
            "dell-lc-supportassist-status"
        ]
        is DellLcSupportAssistStatus
    )

    cmd_parser, cmd_name, cmd_help = (
        DellLcSupportAssistStatus.register_subcommand(DellLcSupportAssistStatus)
    )

    assert "--action" in cmd_parser.format_help()
    assert cmd_name == "dell-lc-supportassist-status"
    assert "SupportAssist" in cmd_help
