"""Dual-mode-style coverage for DellLCService.ExposeiSMInstallerToHostOS."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
LC_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
ISM_ACTION = "#DellLCService.ExposeiSMInstallerToHostOS"
ISM_TARGET = f"{LC_SERVICE}/Actions/DellLCService.ExposeiSMInstallerToHostOS"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path."""
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@pytest.fixture
def dell_lc_manager():
    """Serve the committed Dell corpus over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []
    remove_action = {"enabled": False}

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        data = json.loads(fixture.read_text(encoding="utf-8"))
        if remove_action["enabled"] and request.path.lower() == LC_SERVICE.lower():
            data = dict(data)
            actions = dict(data.get("Actions") or {})
            actions.pop(ISM_ACTION, None)
            data["Actions"] = actions
        return json.dumps(data)

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/ism-1"
        return json.dumps({"Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/ism-1"}})

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-ism",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests, remove_action


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def test_ism_installer_without_confirm_is_preview_only(dell_lc_manager):
    """The iSM installer action previews by default and does not POST."""
    manager, requests, _remove_action = dell_lc_manager

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
    assert _post_requests(requests) == []


def test_ism_installer_confirm_posts_to_discovered_target(dell_lc_manager):
    """With --confirm the command POSTs to the target from DellLCService."""
    manager, requests, _remove_action = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ISM_ACTION
    assert result.data["target"] == ISM_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == ISM_TARGET.lower()
    assert posts[0].json() == {}


def test_ism_installer_confirm_with_dry_run_still_does_not_post(dell_lc_manager):
    """--dry_run keeps the command in preview mode even with --confirm."""
    manager, requests, _remove_action = dell_lc_manager

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
    assert _post_requests(requests) == []


def test_ism_installer_resource_uri_override_skips_manager_discovery(dell_lc_manager):
    """A direct DellLCService URI can be used when Manager discovery is ambiguous."""
    manager, requests, _remove_action = dell_lc_manager

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        resource_uri=LC_SERVICE,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["target"] == ISM_TARGET
    assert not any(request.path == "/redfish/v1/Managers" for request in requests)
    assert _post_requests(requests) == []


def test_ism_installer_missing_action_reports_no_post(dell_lc_manager):
    """A BMC without the action returns a structured error and never POSTs."""
    manager, requests, remove_action = dell_lc_manager
    remove_action["enabled"] = True

    result = manager.sync_invoke(
        ApiRequestType.DellLcIsmInstaller,
        "dell-lc-ism-installer",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == f"action '{ISM_ACTION}' not found on DellLCService"
    assert result.data == {"action": ISM_ACTION, "available": []}
    assert _post_requests(requests) == []
