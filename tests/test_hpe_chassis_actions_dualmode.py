"""Dual-mode tests for guarded HPE iLO chassis OEM actions."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_hpe_chassis_actions import HpeChassisActions
from redfish_ctl.redfish_manager import CommandResult

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
            IDracManager(
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


def test_hpe_chassis_actions_list_disable_mctp_without_post(hpe_corpus_mock):
    """Listing discovers DisableMCTPOnServer and omits FactoryResetMCTP."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(ApiRequestType.HpeChassisActions, "hpe-chassis-actions")

    assert isinstance(result, CommandResult)
    assert result.error is None
    actions = {row["Action"]: row for row in result.data}
    assert set(actions) == {"disable-mctp"}
    assert actions["disable-mctp"]["Chassis"] == "/redfish/v1/Chassis/1"
    assert actions["disable-mctp"]["Target"] == (
        "/redfish/v1/Chassis/1/Actions/Oem/Hpe/"
        "HpeServerChassis.DisableMCTPOnServer"
    )
    assert "FactoryResetMCTP" not in {
        row["FullType"].rsplit(".", 1)[-1] for row in result.data
    }
    assert _post_requests(service) == []


def test_hpe_chassis_action_dry_runs_by_default(hpe_corpus_mock):
    """A selected HPE chassis action resolves the target but does not POST by default."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.HpeChassisActions,
        "hpe-chassis-actions",
        action="disable-mctp",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["requires_confirm"] is True
    assert result.data["blocked"] == "HPE chassis action requires --confirm"
    assert result.data["level"] == "destructive"
    assert result.data["payload"] == {}
    assert result.data["target"] == (
        "/redfish/v1/Chassis/1/Actions/Oem/Hpe/"
        "HpeServerChassis.DisableMCTPOnServer"
    )
    assert _post_requests(service) == []


def test_hpe_chassis_action_confirm_posts_selected_target(hpe_corpus_mock):
    """--confirm posts exactly one selected HPE chassis action."""
    manager, service = hpe_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.HpeChassisActions,
        "hpe-chassis-actions",
        action="disable-mctp",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/chassis/1/actions/oem/hpe/"
        "hpeserverchassis.disablemctponserver"
    )
    assert posts[0].json() == {}


def test_hpe_chassis_action_missing_target_reports_without_post(redfish_mock_factory):
    """A fixture without HPE chassis OEM actions reports an error and no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.HpeChassisActions,
        "hpe-chassis-actions",
        action="disable-mctp",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "HPE chassis action not found: disable-mctp"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_hpe_chassis_actions_exposes_cli_entrypoint():
    """The hpe-chassis-actions command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.HpeChassisActions]["hpe-chassis-actions"] is (
        HpeChassisActions
    )

    cmd_parser, cmd_name, cmd_help = HpeChassisActions.register_subcommand(
        HpeChassisActions
    )

    assert "--action" in cmd_parser.format_help()
    assert cmd_name == "hpe-chassis-actions"
    assert "HPE" in cmd_help
