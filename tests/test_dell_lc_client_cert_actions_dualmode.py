"""Dual-mode tests for Dell LC client-certificate actions."""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.dell_lc.cmd_dell_lc_client_cert_actions import (
    DellLcClientCertActions,
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
DELETE_CERTS_TARGET = (
    f"{LC_SERVICE}/Actions/DellLCService.DeleteAutoDiscoveryClientCerts"
)
DELETE_KEY_TARGET = (
    f"{LC_SERVICE}/Actions/DellLCService.DeleteAutoDiscoveryServerPublicKey"
)
DOWNLOAD_TARGET = f"{LC_SERVICE}/Actions/DellLCService.DownloadClientCerts"


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


def test_client_cert_actions_list_targets_without_post(dell_lc_mock):
    """With no selected mode, the command reports all certificate targets."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClientCertActions,
        "dell-lc-client-cert-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["actions"]["delete-client-certs"] == {
        "action": "#DellLCService.DeleteAutoDiscoveryClientCerts",
        "target": DELETE_CERTS_TARGET,
        "payload": {},
    }
    assert result.data["actions"]["delete-server-key"] == {
        "action": "#DellLCService.DeleteAutoDiscoveryServerPublicKey",
        "target": DELETE_KEY_TARGET,
        "payload": {},
    }
    assert result.data["actions"]["download-client-certs"] == {
        "action": "#DellLCService.DownloadClientCerts",
        "target": DOWNLOAD_TARGET,
        "payload": {},
    }
    assert _post_requests(service) == []


def test_client_cert_action_defaults_to_dry_run(dell_lc_mock):
    """Selecting a mode previews the action and does not POST by default."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClientCertActions,
        "dell-lc-client-cert-actions",
        mode="delete-client-certs",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["action"] == (
        "#DellLCService.DeleteAutoDiscoveryClientCerts"
    )
    assert result.data["target"] == DELETE_CERTS_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["mode"] == "delete-client-certs"
    assert _post_requests(service) == []


def test_client_cert_action_confirm_posts_selected_target(dell_lc_mock):
    """--confirm POSTs the selected action with an empty payload."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClientCertActions,
        "dell-lc-client-cert-actions",
        mode="delete-server-key",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == (
        "#DellLCService.DeleteAutoDiscoveryServerPublicKey"
    )
    assert result.data["target"] == DELETE_KEY_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["lc_service"] == LC_SERVICE
    assert result.data["mode"] == "delete-server-key"
    assert len(posts) == 1
    assert posts[0].path.lower() == DELETE_KEY_TARGET.lower()
    assert posts[0].json() == {}


def test_client_cert_action_dry_run_overrides_confirm(dell_lc_mock):
    """Explicit dry-run wins over --confirm and avoids the POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcClientCertActions,
        "dell-lc-client-cert-actions",
        mode="download-client-certs",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["target"] == DOWNLOAD_TARGET
    assert result.data["blocked"] is None
    assert _post_requests(service) == []


def test_client_cert_actions_report_missing_actions_without_post(dell_lc_mock):
    """A service missing the certificate actions reports the miss."""
    manager, service = dell_lc_mock
    body = copy.deepcopy(service._state(LC_SERVICE))
    for action in (
        "#DellLCService.DeleteAutoDiscoveryClientCerts",
        "#DellLCService.DeleteAutoDiscoveryServerPublicKey",
        "#DellLCService.DownloadClientCerts",
    ):
        body["Actions"].pop(action)
    _overlay_lc_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellLcClientCertActions,
        "dell-lc-client-cert-actions",
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "actions not found: #DellLCService.DeleteAutoDiscoveryClientCerts, "
        "#DellLCService.DeleteAutoDiscoveryServerPublicKey, "
        "#DellLCService.DownloadClientCerts"
    )
    assert LC_SERVICE in result.data["checked"]
    assert _post_requests(service) == []


def test_client_cert_actions_are_registered_and_classified():
    """The command is registered and its actions are guarded."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.DellLcClientCertActions][
        "dell-lc-client-cert-actions"
    ] is DellLcClientCertActions

    for action in (
        "#DellLCService.DeleteAutoDiscoveryClientCerts",
        "#DellLCService.DeleteAutoDiscoveryServerPublicKey",
        "#DellLCService.DownloadClientCerts",
    ):
        assert classify(action) is Destructiveness.DESTRUCTIVE
