"""Dual-mode-style coverage for Dell switch connection refresh."""
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.network.cmd_dell_switch_port_refresh import (
    DellSwitchPortRefresh,
)
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
SWITCH_SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSwitchConnectionService"
)
SWITCH_TARGET = (
    f"{SWITCH_SERVICE}/Actions/"
    "DellSwitchConnectionService.ServerPortConnectionRefresh"
)
SWITCH_COLLECTION = (
    "/redfish/v1/Systems/System.Embedded.1/NetworkPorts/Oem/Dell/"
    "DellSwitchConnections"
)


@pytest.fixture
def dell_switch_manager():
    """Serve the committed Dell XR8620t corpus over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
        vendor="dell",
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-switch",
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


def test_dell_switch_port_refresh_lists_target_without_post(dell_switch_manager):
    """The default command lists the refresh target and connection summary."""
    manager, service = dell_switch_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSwitchPortRefresh,
        "dell-switch-port-refresh",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == [{
        "System": "System.Embedded.1",
        "SystemUri": "/redfish/v1/Systems/System.Embedded.1",
        "Service": SWITCH_SERVICE,
        "Target": SWITCH_TARGET,
        "Connections": {
            "Uri": SWITCH_COLLECTION,
            "Count": 9,
            "StaleData": ["NotStale"],
        },
    }]
    assert _post_requests(service) == []


def test_dell_switch_port_refresh_dry_run_resolves_target_without_post(
    dell_switch_manager,
):
    """--dry_run resolves the refresh action through the standard guard."""
    manager, service = dell_switch_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSwitchPortRefresh,
        "dell-switch-port-refresh",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == (
        "#DellSwitchConnectionService.ServerPortConnectionRefresh"
    )
    assert result.data["target"] == SWITCH_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "reversible"
    assert result.data["blocked"] is None
    assert result.data["service"] == SWITCH_SERVICE
    assert result.data["connections"]["Count"] == 9
    assert _post_requests(service) == []


def test_dell_switch_port_refresh_confirm_posts_target(dell_switch_manager):
    """--confirm sends exactly one refresh POST to the advertised target."""
    manager, service = dell_switch_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSwitchPortRefresh,
        "dell-switch-port-refresh",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == (
        "#DellSwitchConnectionService.ServerPortConnectionRefresh"
    )
    assert result.data["target"] == SWITCH_TARGET
    assert result.data["level"] == "reversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == SWITCH_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_switch_port_refresh_missing_target_reports_without_post(
    redfish_mock_factory,
):
    """A non-Dell fixture reports the missing refresh action and sends no POST."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.DellSwitchPortRefresh,
        "dell-switch-port-refresh",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "DellSwitchConnectionService.ServerPortConnectionRefresh action not found"
    )
    assert result.data == {
        "action": "#DellSwitchConnectionService.ServerPortConnectionRefresh",
        "available": [],
    }
    assert _post_requests(service) == []


def test_dell_switch_port_refresh_is_registered():
    """The dell-switch-port-refresh command is wired into the registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellSwitchPortRefresh][
        "dell-switch-port-refresh"
    ] is DellSwitchPortRefresh

    cmd_parser, cmd_name, cmd_help = (
        DellSwitchPortRefresh.register_subcommand(DellSwitchPortRefresh)
    )

    assert cmd_name == "dell-switch-port-refresh"
    assert "Dell switch-connection" in cmd_help
    assert "--service-uri" in cmd_parser.format_help()
    assert "--confirm" in cmd_parser.format_help()
    assert "--dry_run" in cmd_parser.format_help()


def test_dell_switch_port_refresh_policy_is_reversible():
    """Generic action listings classify the refresh action as reversible."""
    assert classify(
        "#DellSwitchConnectionService.ServerPortConnectionRefresh"
    ) is Destructiveness.REVERSIBLE
