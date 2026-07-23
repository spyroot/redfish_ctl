"""Dual-mode-style coverage for DellSystemManagementService.ShowErrorsOnLCD."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.system.cmd_dell_system_lcd_errors import DellSystemLcdErrors

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
SERVICE = f"{SYSTEM}/Oem/Dell/DellSystemManagementService"
ACTION = "#DellSystemManagementService.ShowErrorsOnLCD"
TARGET = f"{SERVICE}/Actions/DellSystemManagementService.ShowErrorsOnLCD"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


@contextmanager
def _dell_system_lcd_manager(remove_action=False):
    """Serve the Dell corpus over requests-mock.

    :param remove_action: drop ShowErrorsOnLCD from the service fixture.
    :return: tuple of IDracManager and recorded requests.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        data = json.loads(fixture.read_text())
        if remove_action and request.path.lower() == SERVICE.lower():
            data["Actions"].pop(ACTION, None)
        return json.dumps(data)

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/lcd-1"
        return json.dumps(
            {"Task": {"@odata.id": "/redfish/v1/TaskService/Tasks/lcd-1"}}
        )

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = IDracManager(
            idrac_ip="mock-dell-system-lcd",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport."""
    return [request for request in requests if request.method == "POST"]


def test_dell_system_lcd_errors_without_confirm_is_preview_only():
    """ShowErrorsOnLCD resolves its target but does not POST without --confirm."""
    with _dell_system_lcd_manager() as (manager, requests):
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
    assert _post_requests(requests) == []


def test_dell_system_lcd_errors_confirm_posts_empty_payload():
    """--confirm POSTs the empty ShowErrorsOnLCD payload to the action target."""
    with _dell_system_lcd_manager() as (manager, requests):
        result = manager.sync_invoke(
            ApiRequestType.DellSystemLcdErrors,
            "dell-system-lcd-errors",
            confirm=True,
        )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == ACTION
    assert result.data["target"] == TARGET
    assert result.data["level"] == "destructive"
    assert result.data["task_id"] == "lcd-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == TARGET.lower()
    assert posts[0].json() == {}


def test_dell_system_lcd_errors_confirm_dry_run_still_does_not_post():
    """--dry_run remains a no-POST preview even when --confirm is also present."""
    with _dell_system_lcd_manager() as (manager, requests):
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
    assert _post_requests(requests) == []


def test_dell_system_lcd_errors_reports_missing_action_without_post():
    """A Dell service without ShowErrorsOnLCD reports the absent action."""
    with _dell_system_lcd_manager(remove_action=True) as (manager, requests):
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
    assert _post_requests(requests) == []


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
