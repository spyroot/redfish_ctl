"""Dual-mode tests for clearing Dell LC provisioning-server settings."""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_clear_provisioning import (
    DellLcClearProvisioningServer,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
LC_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
CLEAR_TARGET = (
    f"{LC_SERVICE}/Actions/"
    "DellLCService.ClearProvisioningServer"
)


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
            RedfishManagerBase(
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


def test_clear_provisioning_dry_runs_by_default(dell_lc_mock):
    """The corpus-backed action target is resolved without POSTing."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClearProvisioningServer,
        "dell-lc-clear-provisioning",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == "#DellLCService.ClearProvisioningServer"
    assert result.data["target"] == CLEAR_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert _post_requests(service) == []


def test_clear_provisioning_confirm_posts_empty_payload(dell_lc_mock):
    """--confirm POSTs exactly one empty-body clear action."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClearProvisioningServer,
        "dell-lc-clear-provisioning",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert len(posts) == 1
    assert posts[0].path.lower() == CLEAR_TARGET.lower()
    assert posts[0].json() == {}


def test_clear_provisioning_dry_run_overrides_confirm(dell_lc_mock):
    """Explicit dry-run wins over --confirm and avoids the POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClearProvisioningServer,
        "dell-lc-clear-provisioning",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["target"] == CLEAR_TARGET
    assert _post_requests(service) == []


def test_clear_provisioning_reports_missing_action_without_post(dell_lc_mock):
    """A service missing ClearProvisioningServer reports the miss."""
    manager, service = dell_lc_mock
    body = copy.deepcopy(service._state(LC_SERVICE))
    body["Actions"].pop("#DellLCService.ClearProvisioningServer")
    _overlay_lc_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellLcClearProvisioningServer,
        "dell-lc-clear-provisioning",
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "action '#DellLCService.ClearProvisioningServer' not found"
    )
    assert result.data["action"] == "#DellLCService.ClearProvisioningServer"
    assert LC_SERVICE in result.data["checked"]
    assert _post_requests(service) == []


def test_clear_provisioning_is_registered_and_classified():
    """The command is registered and classified as guarded."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellLcClearProvisioningServer][
        "dell-lc-clear-provisioning"
    ] is DellLcClearProvisioningServer

    assert classify("#DellLCService.ClearProvisioningServer") is (
        Destructiveness.DESTRUCTIVE
    )
