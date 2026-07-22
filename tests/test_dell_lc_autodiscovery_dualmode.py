"""Dual-mode tests for Dell LC auto-discovery actions."""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_autodiscovery import DellLcAutoDiscovery
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
AUTO_TARGET = (
    f"{LC_SERVICE}/Actions/DellLCService.ReInitiateAutoDiscovery"
)
DHS_TARGET = f"{LC_SERVICE}/Actions/DellLCService.ReInitiateDHS"


@pytest.fixture
def dell_lc_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus."""
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
    """Return POST requests recorded by the mock Redfish service."""
    return [request for request in service.requests if request.method == "POST"]


def _overlay_lc_service(service, body):
    """Overlay DellLCService under both common request casings."""
    service._overlay[LC_SERVICE] = body
    service._overlay[LC_SERVICE.lower()] = body


def test_autodiscovery_lists_targets_without_post(dell_lc_mock):
    """With no --perform value, the command reports both action targets."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["actions"]["auto-discovery"] == {
        "target": AUTO_TARGET,
        "allowed_perform_auto_discovery": ["NextBoot", "Now", "Off"],
        "action": "#DellLCService.ReInitiateAutoDiscovery",
    }
    assert result.data["actions"]["dhs"] == {
        "target": DHS_TARGET,
        "allowed_perform_auto_discovery": ["NextBoot", "Now", "Off"],
        "action": "#DellLCService.ReInitiateDHS",
    }
    assert _post_requests(service) == []


def test_autodiscovery_perform_defaults_to_dry_run(dell_lc_mock):
    """A PerformAutoDiscovery value previews by default and does not POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        perform_auto_discovery="NextBoot",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == "#DellLCService.ReInitiateAutoDiscovery"
    assert result.data["target"] == AUTO_TARGET
    assert result.data["payload"] == {"PerformAutoDiscovery": "NextBoot"}
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["mode"] == "auto-discovery"
    assert _post_requests(service) == []


def test_autodiscovery_confirm_posts_selected_action(dell_lc_mock):
    """--confirm POSTs one ReInitiateAutoDiscovery payload."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        perform_auto_discovery="Now",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellLCService.ReInitiateAutoDiscovery"
    assert result.data["target"] == AUTO_TARGET
    assert result.data["task_id"] == service.JOB_ID
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert len(posts) == 1
    assert posts[0].path.lower() == AUTO_TARGET.lower()
    assert posts[0].json() == {"PerformAutoDiscovery": "Now"}


def test_autodiscovery_dhs_mode_posts_dhs_target(dell_lc_mock):
    """The DHS mode selects DellLCService.ReInitiateDHS."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        mode="dhs",
        perform_auto_discovery="Off",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == "#DellLCService.ReInitiateDHS"
    assert result.data["target"] == DHS_TARGET
    assert result.data["mode"] == "dhs"
    assert len(posts) == 1
    assert posts[0].path.lower() == DHS_TARGET.lower()
    assert posts[0].json() == {"PerformAutoDiscovery": "Off"}


def test_autodiscovery_dry_run_overrides_confirm(dell_lc_mock):
    """Explicit dry-run wins over --confirm and avoids the POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        perform_auto_discovery="Now",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == AUTO_TARGET
    assert _post_requests(service) == []


def test_autodiscovery_rejects_invalid_perform_value(dell_lc_mock):
    """Inline allowable values reject unsupported PerformAutoDiscovery values."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        perform_auto_discovery="Immediate",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellLCService.ReInitiateAutoDiscovery "
        "PerformAutoDiscovery: Immediate; allowed: NextBoot, Now, Off"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "PerformAutoDiscovery",
            "value": "Immediate",
            "allowed": ["NextBoot", "Now", "Off"],
        }
    ]
    assert _post_requests(service) == []


def test_autodiscovery_reports_missing_actions_without_post(dell_lc_mock):
    """A service missing both re-initiate actions reports the miss."""
    manager, service = dell_lc_mock
    body = copy.deepcopy(service._state(LC_SERVICE))
    body["Actions"].pop("#DellLCService.ReInitiateAutoDiscovery")
    body["Actions"].pop("#DellLCService.ReInitiateDHS")
    _overlay_lc_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellLcAutoDiscovery,
        "dell-lc-autodiscovery",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "actions not found: #DellLCService.ReInitiateAutoDiscovery, "
        "#DellLCService.ReInitiateDHS"
    )
    assert LC_SERVICE in result.data["checked"]
    assert _post_requests(service) == []


def test_autodiscovery_is_registered_and_classified():
    """The command is registered and both actions are guarded."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellLcAutoDiscovery][
        "dell-lc-autodiscovery"
    ] is DellLcAutoDiscovery

    assert classify("#DellLCService.ReInitiateAutoDiscovery") is (
        Destructiveness.DESTRUCTIVE
    )
    assert classify("#DellLCService.ReInitiateDHS") is (
        Destructiveness.DESTRUCTIVE
    )
