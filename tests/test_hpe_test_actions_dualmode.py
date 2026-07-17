"""Dual-mode tests for HPE iLO diagnostic/test actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.oem.cmd_hpe_test_actions import HpeTestActions
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
HPE_CORPUS = corpus_dir(REPO_ROOT / "tests" / "hpe_dl360_corpus.tar.gz", "10.43.3.209")


@pytest.fixture
def hpe_corpus_mock():
    """Return a manager and mock service backed by the full HPE DL360 corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(HPE_CORPUS, index=_build_fixture_index(HPE_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            RedfishManagerBase(
                idrac_ip="mock-hpe-dl360",
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


def test_hpe_test_actions_list_corpus_targets_without_post(hpe_corpus_mock):
    """Listing discovers HPE DirectoryTest, SNMP, mail, and syslog actions."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(ApiRequestType.HpeTestActions, "hpe-test-actions")

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {
        "directory-start",
        "directory-stop",
        "snmp-alert",
        "mail-alert",
        "syslog-alert",
    }
    assert actions["directory-start"]["Resource"] == (
        "/redfish/v1/AccountService/DirectoryTest"
    )
    assert actions["snmp-alert"]["Target"] == (
        "/redfish/v1/Managers/1/SnmpService/Actions/"
        "HpeiLOSnmpService.SendSNMPTestAlert"
    )
    assert actions["mail-alert"]["Target"] == (
        "/redfish/v1/Managers/1/NetworkProtocol/Actions/Oem/Hpe/"
        "HpeiLOManagerNetworkService.SendTestAlertMail"
    )
    assert actions["syslog-alert"]["Target"] == (
        "/redfish/v1/Managers/1/NetworkProtocol/Actions/Oem/Hpe/"
        "HpeiLOManagerNetworkService.SendTestSyslog"
    )
    assert _post_requests(service) == []


def test_hpe_test_action_dry_runs_by_default(hpe_corpus_mock):
    """A selected HPE test action resolves the target but does not POST by default."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.HpeTestActions,
        "hpe-test-actions",
        action="snmp-alert",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == "HPE test action requires --confirm"
    assert result.data["level"] == "reversible"
    assert result.data["payload"] == {}
    assert result.data["target"] == (
        "/redfish/v1/Managers/1/SnmpService/Actions/"
        "HpeiLOSnmpService.SendSNMPTestAlert"
    )
    assert _post_requests(service) == []


def test_hpe_test_action_confirm_posts_selected_target(hpe_corpus_mock):
    """--confirm posts exactly one selected HPE test action."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.HpeTestActions,
        "hpe-test-actions",
        action="syslog-alert",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/1/networkprotocol/actions/oem/hpe/"
        "hpeilomanagernetworkservice.sendtestsyslog"
    )
    assert posts[0].json() == {}


def test_hpe_test_action_missing_target_reports_without_post(redfish_mock_factory):
    """A fixture without HPE test-action resources reports an error and no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.HpeTestActions,
        "hpe-test-actions",
        action="snmp-alert",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "HPE test action not found: snmp-alert"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_hpe_test_actions_exposes_cli_entrypoint():
    """The hpe-test-actions command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.HpeTestActions]["hpe-test-actions"] is (
        HpeTestActions
    )

    cmd_parser, cmd_name, cmd_help = HpeTestActions.register_subcommand(
        HpeTestActions
    )

    assert "--action" in cmd_parser.format_help()
    assert cmd_name == "hpe-test-actions"
    assert "HPE" in cmd_help
