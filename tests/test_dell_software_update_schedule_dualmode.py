"""Dual-mode-style coverage for Dell software update schedule actions."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.delloem.cmd_dell_software_update_schedule import (
    DellSoftwareUpdateSchedule,
)
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

DELL_CORPUS = corpus_dir(
    Path(__file__).parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
DELL_INDEX = {path.name.lower(): path for path in DELL_CORPUS.glob("*.json")}
SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
SET_TARGET = f"{SERVICE}/Actions/DellSoftwareInstallationService.SetUpdateSchedule"
CLEAR_TARGET = f"{SERVICE}/Actions/DellSoftwareInstallationService.ClearUpdateSchedule"


def _fixture_for_path(path):
    """Return the extracted Dell fixture matching a Redfish path.

    :param path: request path from requests-mock.
    :return: fixture path, or None when the corpus lacks the resource.
    """
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return DELL_INDEX.get(name.lower())


def _service_body():
    """Return the DellSoftwareInstallationService fixture body.

    :return: parsed DellSoftwareInstallationService JSON.
    """
    return json.loads(_fixture_for_path(SERVICE).read_text())


@pytest.fixture
def dell_software_manager():
    """Serve the committed Dell corpus over requests-mock.

    :return: tuple of RedfishManagerBase, recorded requests, and GET overrides.
    """
    requests_mock = pytest.importorskip("requests_mock")
    requests = []
    overrides = {}

    def get_cb(request, context):
        requests.append(request)
        override = overrides.get(request.path.lower())
        if override is not None:
            context.status_code = 200
            return json.dumps(override)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    def post_cb(request, context):
        requests.append(request)
        context.status_code = 202
        context.headers["Location"] = "/redfish/v1/TaskService/Tasks/software-schedule-1"
        return json.dumps({
            "Task": {
                "@odata.id": "/redfish/v1/TaskService/Tasks/software-schedule-1"
            }
        })

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        mocker.post(requests_mock.ANY, text=post_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-dell-software",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests, overrides


def _post_requests(requests):
    """Return POST requests recorded by the mock Redfish transport.

    :param requests: recorded requests-mock request objects.
    :return: list of POST requests.
    """
    return [request for request in requests if request.method == "POST"]


def test_dell_software_update_schedule_lists_targets(dell_software_manager):
    """Omitting --action lists set/clear targets without POSTing."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert {
        (row["Action"], row["Target"])
        for row in result.data
    } == {
        ("set", SET_TARGET),
        ("clear", CLEAR_TARGET),
    }
    set_row = next(row for row in result.data if row["Action"] == "set")
    assert set_row["AllowableValues"]["ShareType"] == [
        "CIFS",
        "FTP",
        "HTTP",
        "HTTPS",
        "NFS",
    ]
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_set_previews_payload(dell_software_manager):
    """SetUpdateSchedule resolves and previews payloads by default."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        payload_json='{"ShareName": "/repo", "Password": "secret"}',
        share_type="HTTP",
        apply_reboot="NoReboot",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.SetUpdateSchedule"
    assert result.data["target"] == SET_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {
        "ShareName": "/repo",
        "Password": "********",
        "ShareType": "HTTP",
        "ApplyReboot": "NoReboot",
    }
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_set_confirm_posts(dell_software_manager):
    """--confirm POSTs a SetUpdateSchedule payload to the discovered target."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        payload_json='{"ShareName": "/repo"}',
        share_type="HTTPS",
        apply_reboot="RebootRequired",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.SetUpdateSchedule"
    assert result.data["target"] == SET_TARGET
    assert result.data["task_id"] == "software-schedule-1"
    assert len(posts) == 1
    assert posts[0].path.lower() == SET_TARGET.lower()
    assert posts[0].json() == {
        "ShareName": "/repo",
        "ShareType": "HTTPS",
        "ApplyReboot": "RebootRequired",
    }


def test_dell_software_update_schedule_set_dry_run_overrides_confirm(
    dell_software_manager,
):
    """--dry_run keeps SetUpdateSchedule from POSTing even with --confirm."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        share_type="NFS",
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == SET_TARGET
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_rejects_invalid_allowable(
    dell_software_manager,
):
    """Inline allowable values reject invalid SetUpdateSchedule enum values."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        share_type="TFTP",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellSoftwareInstallationService.SetUpdateSchedule "
        "ShareType: TFTP; allowed: CIFS, FTP, HTTP, HTTPS, NFS"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "ShareType",
            "value": "TFTP",
            "allowed": ["CIFS", "FTP", "HTTP", "HTTPS", "NFS"],
        }
    ]
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_clear_previews(dell_software_manager):
    """ClearUpdateSchedule previews by default without POSTing."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="clear",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.ClearUpdateSchedule"
    assert result.data["target"] == CLEAR_TARGET
    assert result.data["payload"] == {}
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_clear_confirm_posts_empty_payload(
    dell_software_manager,
):
    """--confirm POSTs an empty ClearUpdateSchedule body."""
    manager, requests, _ = dell_software_manager

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="clear",
        confirm=True,
    )

    posts = _post_requests(requests)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.ClearUpdateSchedule"
    assert result.data["target"] == CLEAR_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == CLEAR_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_software_update_schedule_reports_missing_action(
    dell_software_manager,
):
    """A service without SetUpdateSchedule reports the available schedule action."""
    manager, requests, overrides = dell_software_manager
    body = _service_body()
    body["Actions"].pop("#DellSoftwareInstallationService.SetUpdateSchedule")
    overrides[SERVICE.lower()] = body

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        share_type="HTTP",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell software update schedule action not found: set"
    assert [row["Action"] for row in result.data["available"]] == ["clear"]
    assert _post_requests(requests) == []


def test_dell_software_update_schedule_rejects_empty_set_payload(
    dell_software_manager,
):
    """SetUpdateSchedule requires at least one payload field."""
    manager, requests, _ = dell_software_manager

    with pytest.raises(InvalidArgument, match="--action set requires"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateSchedule,
            "dell-software-update-schedule",
            action="set",
        )

    assert _post_requests(requests) == []


def test_dell_software_update_schedule_rejects_non_object_json(
    dell_software_manager,
):
    """The payload JSON must be an object."""
    manager, requests, _ = dell_software_manager

    with pytest.raises(InvalidArgument, match="JSON object"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateSchedule,
            "dell-software-update-schedule",
            action="set",
            payload_json='["not-object"]',
        )

    assert _post_requests(requests) == []


def test_dell_software_update_schedule_is_registered():
    """The command is wired into the command registry."""
    registry = RedfishManagerBase().get_registry()
    assert (
        registry[ApiRequestType.DellSoftwareUpdateSchedule][
            "dell-software-update-schedule"
        ]
        is DellSoftwareUpdateSchedule
    )

    cmd_parser, cmd_name, cmd_help = DellSoftwareUpdateSchedule.register_subcommand(
        DellSoftwareUpdateSchedule
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-software-update-schedule"
    assert "schedule" in cmd_help
    assert "--action" in help_text
    assert "--payload-json" in help_text
    assert "--confirm" in help_text
