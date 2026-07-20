"""Dual-mode-style coverage for DellBIOSService.DeviceRecovery."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}

SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
BIOS_SERVICE = f"{SYSTEM}/Oem/Dell/DellBIOSService"
ACTION = "#DellBIOSService.DeviceRecovery"
TARGET = f"{BIOS_SERVICE}/Actions/DellBIOSService.DeviceRecovery"
TASK_ID = "JID_000000000001"


def _fixture_for_path(path):
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _post_requests(requests):
    return [request for request in requests if request.method == "POST"]


@contextmanager
def _mock_dell_corpus(remove_action=False):
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        body = json.loads(fixture.read_text())
        if remove_action and request.path.lower() == BIOS_SERVICE.lower():
            body["Actions"].pop(ACTION, None)
        context.status_code = 200
        return json.dumps(body)

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = f"/redfish/v1/TaskService/Tasks/{TASK_ID}"
        return ""

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def test_dell_bios_device_recovery_lists_corpus_target():
    """The command lists the DellBIOSService target advertised by the corpus."""
    with _mock_dell_corpus() as (manager, requests):
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
    assert _post_requests(requests) == []


def test_dell_bios_device_recovery_previews_by_default():
    """DeviceRecovery defaults to a destructive-action dry-run."""
    with _mock_dell_corpus() as (manager, requests):
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
    assert _post_requests(requests) == []


def test_dell_bios_device_recovery_confirm_posts_device_payload():
    """With --confirm the command POSTs the advertised DeviceRecovery payload."""
    with _mock_dell_corpus() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellBiosDeviceRecovery,
            "dell-bios-device-recovery",
            confirm=True,
        )

    posts = _post_requests(requests)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["task_id"] == TASK_ID
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path == TARGET.lower()
    assert posts[0].json() == {"Device": "BIOS"}


def test_dell_bios_device_recovery_rejects_unadvertised_device():
    """Payload validation rejects Device values outside the service metadata."""
    with _mock_dell_corpus() as (manager, requests):
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
    assert _post_requests(requests) == []


def test_dell_bios_device_recovery_missing_action_reports_available():
    """A service without DeviceRecovery reports the missing action and never POSTs."""
    with _mock_dell_corpus(remove_action=True) as (manager, requests):
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
    assert _post_requests(requests) == []
