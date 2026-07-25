"""Dual-mode-style coverage for Dell vFlash state changes."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_vflash_state import DellVFlashStateChange
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
PERSISTENT_STORAGE_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellPersistentStorageService"
)
VFLASH_ACTION = "#DellPersistentStorageService.VFlashStateChange"
VFLASH_TARGET = (
    f"{PERSISTENT_STORAGE_SERVICE}/Actions/"
    "DellPersistentStorageService.VFlashStateChange"
)


@pytest.fixture
def dell_vflash_mock():
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
                idrac_ip="mock-dell-vflash",
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


def _overlay_persistent_service(service, body):
    """Overlay DellPersistentStorageService under both common request casings.

    :param service: the recording MockRedfishService.
    :param body: replacement persistent-storage service body.
    """
    service._overlay[PERSISTENT_STORAGE_SERVICE] = body
    service._overlay[PERSISTENT_STORAGE_SERVICE.lower()] = body


def test_dell_vflash_state_lists_target_without_posting(dell_vflash_mock):
    """With no requested state, the command lists the VFlash target only."""
    manager, service = dell_vflash_mock

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
    assert _post_requests(service) == []


def test_dell_vflash_state_without_confirm_is_preview_only(dell_vflash_mock):
    """VFlashStateChange resolves the target but does not POST without confirm."""
    manager, service = dell_vflash_mock

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
    assert _post_requests(service) == []


def test_dell_vflash_state_confirm_posts_requested_state(dell_vflash_mock):
    """--confirm POSTs the state; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_vflash_mock

    result = manager.sync_invoke(
        ApiRequestType.DellVFlashStateChange,
        "dell-vflash-state",
        requested_state="Disable",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == VFLASH_ACTION
    assert result.data["target"] == VFLASH_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == VFLASH_TARGET.lower()
    assert posts[0].json() == {"RequestedState": "Disable"}


def test_dell_vflash_state_rejects_invalid_requested_state(dell_vflash_mock):
    """Inline allowable values reject an unsupported RequestedState before POST."""
    manager, service = dell_vflash_mock

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
    assert _post_requests(service) == []


def test_dell_vflash_state_reports_missing_action_without_posting(dell_vflash_mock):
    """A persistent-storage resource without VFlashStateChange fails closed."""
    manager, service = dell_vflash_mock
    body = copy.deepcopy(service._state(PERSISTENT_STORAGE_SERVICE))
    body.get("Actions", {}).pop(VFLASH_ACTION, None)
    _overlay_persistent_service(service, body)

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
    assert _post_requests(service) == []


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
