"""Dual-mode-style coverage for DellSystemManagementService.ShowErrorsOnLCD."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.system.cmd_dell_system_lcd_errors import DellSystemLcdErrors

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
SERVICE = f"{SYSTEM}/Oem/Dell/DellSystemManagementService"
ACTION = "#DellSystemManagementService.ShowErrorsOnLCD"
TARGET = f"{SERVICE}/Actions/DellSystemManagementService.ShowErrorsOnLCD"


@pytest.fixture
def dell_system_lcd_mock():
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
                idrac_ip="mock-dell-system-lcd",
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


def _overlay_management_service(service, body):
    """Overlay DellSystemManagementService under both common request casings.

    :param service: the recording MockRedfishService.
    :param body: replacement system-management-service body.
    """
    service._overlay[SERVICE] = body
    service._overlay[SERVICE.lower()] = body


def test_dell_system_lcd_errors_without_confirm_is_preview_only(
    dell_system_lcd_mock,
):
    """ShowErrorsOnLCD resolves its target but does not POST without --confirm."""
    manager, service = dell_system_lcd_mock

    result = manager.sync_invoke(
        ApiRequestType.DellSystemLcdErrors,
        "dell-system-lcd-errors",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["system"] == SYSTEM
    assert result.data["system_management_service"] == SERVICE
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {}
    assert _post_requests(service) == []


def test_dell_system_lcd_errors_confirm_posts_empty_payload(
    dell_system_lcd_mock,
):
    """--confirm POSTs ShowErrorsOnLCD; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_system_lcd_mock

    result = manager.sync_invoke(
        ApiRequestType.DellSystemLcdErrors,
        "dell-system-lcd-errors",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET.lower()
    assert posts[0].json() == {}


def test_dell_system_lcd_errors_confirm_dry_run_still_does_not_post(
    dell_system_lcd_mock,
):
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    manager, service = dell_system_lcd_mock

    result = manager.sync_invoke(
        ApiRequestType.DellSystemLcdErrors,
        "dell-system-lcd-errors",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == TARGET
    assert _post_requests(service) == []


def test_dell_system_lcd_errors_reports_missing_action_without_post(
    dell_system_lcd_mock,
):
    """A Dell service without ShowErrorsOnLCD reports the absent action."""
    manager, service = dell_system_lcd_mock
    body = copy.deepcopy(service._state(SERVICE))
    body["Actions"].pop(ACTION, None)
    _overlay_management_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellSystemLcdErrors,
        "dell-system-lcd-errors",
        system_uri=SYSTEM,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        f"action '{ACTION}' not found on DellSystemManagementService"
    )
    assert result.data["action"] == ACTION
    assert result.data["attempted"] == [SERVICE]
    assert "RebootChassisManager" in result.data["available"]
    assert _post_requests(service) == []


def test_dell_system_lcd_errors_policy_is_destructive():
    """ShowErrorsOnLCD cannot fire unless explicitly confirmed."""
    assert classify(ACTION) is Destructiveness.DESTRUCTIVE


def test_dell_system_lcd_errors_exposes_cli_entrypoint():
    """The dell-system-lcd-errors command is wired into the package registry."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellSystemLcdErrors]["dell-system-lcd-errors"] is (
        DellSystemLcdErrors
    )

    cmd_parser, cmd_name, cmd_help = DellSystemLcdErrors.register_subcommand(
        DellSystemLcdErrors
    )

    assert "--system-uri" in cmd_parser.format_help()
    assert "--confirm" in cmd_parser.format_help()
    assert cmd_name == "dell-system-lcd-errors"
    assert "Dell" in cmd_help
