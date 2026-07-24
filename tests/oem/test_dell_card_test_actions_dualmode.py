"""Dual-mode tests for Dell iDRAC card diagnostic/test actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_card_test_actions import DellCardTestActions
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
                         "10.252.252.209")


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the full Dell XR8620t corpus.

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
                idrac_ip="mock-dell-xr8620t",
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


def test_dell_card_test_actions_list_corpus_targets_without_post(dell_corpus_mock):
    """Listing discovers Dell iDRAC card email, SNMP, and rsyslog actions."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardTestActions,
        "dell-card-test-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {"email-alert", "snmp-trap", "rsyslog"}
    assert actions["email-alert"]["Resource"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
    )
    assert actions["email-alert"]["Target"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
        "DelliDRACCardService/Actions/DelliDRACCardService.SendTestEmailAlert"
    )
    assert actions["snmp-trap"]["Target"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
        "DelliDRACCardService/Actions/DelliDRACCardService.SendTestSNMPTrap"
    )
    assert actions["rsyslog"]["Target"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
        "DelliDRACCardService/Actions/"
        "DelliDRACCardService.TestRsyslogServerConnection"
    )
    assert _post_requests(service) == []


def test_dell_card_test_action_dry_runs_by_default(dell_corpus_mock):
    """A selected Dell card test action resolves the target but does not POST."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardTestActions,
        "dell-card-test-actions",
        action="snmp-trap",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == (
        "Dell iDRAC card test action requires --confirm"
    )
    assert result.data["level"] == "reversible"
    assert result.data["payload"] == {}
    assert result.data["target"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
        "DelliDRACCardService/Actions/DelliDRACCardService.SendTestSNMPTrap"
    )
    assert _post_requests(service) == []


def test_dell_card_test_action_confirm_posts_selected_target(dell_corpus_mock):
    """--confirm posts exactly one selected Dell card test action."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellCardTestActions,
        "dell-card-test-actions",
        action="rsyslog",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/idrac.embedded.1/oem/dell/"
        "dellidraccardservice/actions/"
        "dellidraccardservice.testrsyslogserverconnection"
    )
    assert posts[0].json() == {}


def test_dell_card_test_action_missing_target_reports_without_post(redfish_mock_factory):
    """A fixture without Dell card test-action resources reports an error."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellCardTestActions,
        "dell-card-test-actions",
        action="snmp-trap",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell iDRAC card test action not found: snmp-trap"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_dell_card_test_actions_exposes_cli_entrypoint():
    """The dell-card-test-actions command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellCardTestActions][
        "dell-card-test-actions"
    ] is DellCardTestActions

    cmd_parser, cmd_name, cmd_help = DellCardTestActions.register_subcommand(
        DellCardTestActions
    )

    assert "--action" in cmd_parser.format_help()
    assert cmd_name == "dell-card-test-actions"
    assert "Dell" in cmd_help
