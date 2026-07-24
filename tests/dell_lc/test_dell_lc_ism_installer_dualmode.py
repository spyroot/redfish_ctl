"""Dual-mode-style coverage for DellLCService.ExposeiSMInstallerToHostOS."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
LC_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
ISM_ACTION = "#DellLCService.ExposeiSMInstallerToHostOS"
ISM_TARGET = f"{LC_SERVICE}/Actions/DellLCService.ExposeiSMInstallerToHostOS"


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
                idrac_ip="mock-dell-ism",
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


def _overlay_lc_service_without_ism(service):
    """Overlay DellLCService with the iSM action removed, under both casings.

    :param service: the recording MockRedfishService.
    """
    body = copy.deepcopy(service._state(LC_SERVICE))
    body["Actions"].pop(ISM_ACTION, None)
    service._overlay[LC_SERVICE] = body
    service._overlay[LC_SERVICE.lower()] = body


def test_ism_installer_without_confirm_is_preview_only(dell_lc_mock):
    """The iSM installer action previews by default and does not POST."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": ISM_ACTION,
        "target": ISM_TARGET,
        "payload": {},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
    }
    assert _post_requests(service) == []


def test_ism_installer_confirm_posts_to_discovered_target(dell_lc_mock):
    """With --confirm the POST fires; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ISM_ACTION
    assert result.data["target"] == ISM_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == ISM_TARGET.lower()
    assert posts[0].json() == {}


def test_ism_installer_confirm_with_dry_run_still_does_not_post(dell_lc_mock):
    """--dry_run keeps the command in preview mode even with --confirm."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == ISM_TARGET
    assert _post_requests(service) == []


def test_ism_installer_resource_uri_override_skips_manager_discovery(dell_lc_mock):
    """A direct DellLCService URI can be used when Manager discovery is ambiguous."""
    manager, service = dell_lc_mock

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        resource_uri=LC_SERVICE,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["target"] == ISM_TARGET
    assert not any(
        request.path.lower() == "/redfish/v1/managers"
        for request in service.requests
    )
    assert _post_requests(service) == []


def test_ism_installer_missing_action_reports_no_post(dell_lc_mock):
    """A BMC without the action returns a structured error and never POSTs."""
    manager, service = dell_lc_mock
    _overlay_lc_service_without_ism(service)

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == f"action '{ISM_ACTION}' not found on DellLCService"
    assert result.data == {"action": ISM_ACTION, "available": []}
    assert _post_requests(service) == []
