"""Dual-mode-style coverage for DellBIOSService.DeviceRecovery."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)

SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
BIOS_SERVICE = f"{SYSTEM}/Oem/Dell/DellBIOSService"
ACTION = "#DellBIOSService.DeviceRecovery"
TARGET = f"{BIOS_SERVICE}/Actions/DellBIOSService.DeviceRecovery"


@pytest.fixture
def dell_bios_mock():
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
                idrac_ip="mock-dell",
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


def _overlay_bios_service(service, body):
    """Overlay DellBIOSService under both common request casings.

    :param service: the recording MockRedfishService.
    :param body: replacement BIOS-service body.
    """
    service._overlay[BIOS_SERVICE] = body
    service._overlay[BIOS_SERVICE.lower()] = body


def test_dell_bios_device_recovery_lists_corpus_target(dell_bios_mock):
    """The command lists the DellBIOSService target advertised by the corpus."""
    manager, service = dell_bios_mock

    result = manager.sync_invoke(
        ApiRequestType.DellBiosDeviceRecovery,
        "dell-bios-device-recovery",
        list_only=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["action"] == ACTION
    assert result.data["targets"] == [
        {
            "system": SYSTEM,
            "bios_service": BIOS_SERVICE,
            "target": TARGET,
            "devices": ["BIOS"],
        }
    ]
    assert _post_requests(service) == []


def test_dell_bios_device_recovery_previews_by_default(dell_bios_mock):
    """DeviceRecovery defaults to a destructive-action dry-run."""
    manager, service = dell_bios_mock

    result = manager.sync_invoke(
        ApiRequestType.DellBiosDeviceRecovery,
        "dell-bios-device-recovery",
    )

    assert result.error is None
    assert result.data == {
        "dry_run": True,
        "action": ACTION,
        "target": TARGET,
        "payload": {"Device": "BIOS"},
        "level": "destructive",
        "blocked": "destructive action requires --confirm",
        "system": SYSTEM,
        "bios_service": BIOS_SERVICE,
        "device": "BIOS",
    }
    assert _post_requests(service) == []


def test_dell_bios_device_recovery_confirm_posts_device_payload(dell_bios_mock):
    """--confirm POSTs DeviceRecovery; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_bios_mock

    result = manager.sync_invoke(
        ApiRequestType.DellBiosDeviceRecovery,
        "dell-bios-device-recovery",
        confirm=True,
    )

    posts = _post_requests(service)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path == TARGET.lower()
    assert posts[0].json() == {"Device": "BIOS"}


def test_dell_bios_device_recovery_rejects_unadvertised_device(dell_bios_mock):
    """Payload validation rejects Device values outside the service metadata."""
    manager, service = dell_bios_mock

    result = manager.sync_invoke(
        ApiRequestType.DellBiosDeviceRecovery,
        "dell-bios-device-recovery",
        device="BMC",
        confirm=True,
    )

    assert result.error == (
        "invalid value for DellBIOSService.DeviceRecovery Device: BMC; "
        "allowed: BIOS"
    )
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["payload"] == {"Device": "BMC"}
    assert _post_requests(service) == []


def test_dell_bios_device_recovery_missing_action_reports_available(dell_bios_mock):
    """A service without DeviceRecovery reports the missing action and never POSTs."""
    manager, service = dell_bios_mock
    body = copy.deepcopy(service._state(BIOS_SERVICE))
    body["Actions"].pop(ACTION, None)
    _overlay_bios_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellBiosDeviceRecovery,
        "dell-bios-device-recovery",
        confirm=True,
    )

    assert result.error == (
        f"action '{ACTION}' not found on DellBIOSService"
    )
    assert result.data["action"] == ACTION
    assert result.data["available"] == []
    assert result.data["attempted"] == [BIOS_SERVICE]
    assert _post_requests(service) == []
