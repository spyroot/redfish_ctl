"""Dual-mode-style coverage for Dell vFlash state changes."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.oem.cmd_dell_vflash_state import DellVFlashStateChange
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
PERSISTENT_STORAGE_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellPersistentStorageService"
)
VFLASH_ACTION = "#DellPersistentStorageService.VFlashStateChange"
VFLASH_TARGET = (
    f"{PERSISTENT_STORAGE_SERVICE}/Actions/"
    "DellPersistentStorageService.VFlashStateChange"
)


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@contextmanager
def _mock_dell_vflash(persistent_storage_transform=None):
    """Serve the committed Dell corpus over requests-mock.

    :param persistent_storage_transform: optional mutator for the service fixture.
    :return: context yielding IDracManager and recorded requests.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        data = json.loads(fixture.read_text())
        is_service = request.path.rstrip("/").lower() == (
            PERSISTENT_STORAGE_SERVICE.lower()
        )
        if is_service and persistent_storage_transform:
            data = persistent_storage_transform(data)
        context.status_code = 200
        return json.dumps(data)

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/vflash-1"
        return json.dumps({
            "Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/vflash-1"}
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-vflash",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_vflash_state_lists_target_without_posting():
    """With no requested state, the command lists the VFlash target only."""
    with _mock_dell_vflash() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellVFlashStateChange,
            "dell-vflash-state",
        )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "persistent_storage_service": PERSISTENT_STORAGE_SERVICE,
        "action": VFLASH_ACTION,
        "target": VFLASH_TARGET,
        "requested_states": ["Disable", "Enable"],
    }
    assert _post_requests(requests) == []


def test_dell_vflash_state_without_confirm_is_preview_only():
    """VFlashStateChange resolves the target but does not POST without confirm."""
    with _mock_dell_vflash() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellVFlashStateChange,
            "dell-vflash-state",
            requested_state="Enable",
        )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == VFLASH_ACTION
    assert result.data["target"] == VFLASH_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {"RequestedState": "Enable"}
    assert _post_requests(requests) == []


def test_dell_vflash_state_confirm_posts_requested_state():
    """--confirm POSTs the requested state to the discovered action target."""
    with _mock_dell_vflash() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellVFlashStateChange,
            "dell-vflash-state",
            requested_state="Disable",
            confirm=True,
        )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == VFLASH_ACTION
    assert result.data["target"] == VFLASH_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "vflash-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == VFLASH_TARGET.lower()
    assert posts[0].json() == {"RequestedState": "Disable"}


def test_dell_vflash_state_rejects_invalid_requested_state():
    """Inline allowable values reject an unsupported RequestedState before POST."""
    with _mock_dell_vflash() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellVFlashStateChange,
            "dell-vflash-state",
            requested_state="Suspend",
            confirm=True,
        )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellPersistentStorageService.VFlashStateChange "
        "RequestedState: Suspend; allowed: Disable, Enable"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "RequestedState",
            "value": "Suspend",
            "allowed": ["Disable", "Enable"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_vflash_state_reports_missing_action_without_posting():
    """A persistent-storage resource without VFlashStateChange fails closed."""

    def without_vflash_action(data):
        data.get("Actions", {}).pop(VFLASH_ACTION, None)
        return data

    with _mock_dell_vflash(without_vflash_action) as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellVFlashStateChange,
            "dell-vflash-state",
            requested_state="Enable",
            confirm=True,
        )

    assert isinstance(result, CommandResult)
    assert result.error == (
        f"action '{VFLASH_ACTION}' not found on {PERSISTENT_STORAGE_SERVICE}"
    )
    assert VFLASH_ACTION not in result.data["available"]
    assert "AttachPartition" in result.data["available"]
    assert _post_requests(requests) == []


def test_dell_vflash_state_exposes_cli_entrypoint_and_policy():
    """The command is registered and classified as a guarded state change."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellVFlashStateChange][
        "dell-vflash-state"
    ] is DellVFlashStateChange
    assert classify(VFLASH_ACTION) is Destructiveness.DESTRUCTIVE

    cmd_parser, cmd_name, cmd_help = DellVFlashStateChange.register_subcommand(
        DellVFlashStateChange
    )

    assert "--requested-state" in cmd_parser.format_help()
    assert "--confirm" in cmd_parser.format_help()
    assert "--dry_run" in cmd_parser.format_help()
    assert cmd_name == "dell-vflash-state"
    assert "vFlash" in cmd_help


def test_dell_vflash_state_rejects_blank_requested_state():
    """Blank requested-state input is rejected before any POST can be made."""
    with pytest.raises(InvalidArgument, match="requested state cannot be empty"):
        DellVFlashStateChange._payload("  ")
