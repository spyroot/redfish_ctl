"""Dual-mode-style coverage for Dell software update schedule actions."""

import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.delloem.cmd_dell_software_update_schedule import (
    DellSoftwareUpdateSchedule,
)
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
SET_TARGET = f"{SERVICE}/Actions/DellSoftwareInstallationService.SetUpdateSchedule"
CLEAR_TARGET = f"{SERVICE}/Actions/DellSoftwareInstallationService.ClearUpdateSchedule"


@pytest.fixture
def dell_software_mock():
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
                idrac_ip="mock-dell-software",
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


def _overlay_installation_service(service, body):
    """Overlay DellSoftwareInstallationService under both common request casings.

    :param service: the recording MockRedfishService.
    :param body: replacement installation-service body.
    """
    service._overlay[SERVICE] = body
    service._overlay[SERVICE.lower()] = body


def test_dell_software_update_schedule_lists_targets(dell_software_mock):
    """Omitting --action lists set/clear targets without POSTing."""
    manager, service = dell_software_mock

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
    assert _post_requests(service) == []


def test_dell_software_update_schedule_set_previews_payload(dell_software_mock):
    """SetUpdateSchedule resolves and previews payloads by default."""
    manager, service = dell_software_mock

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
    assert _post_requests(service) == []


def test_dell_software_update_schedule_set_confirm_posts(dell_software_mock):
    """--confirm POSTs SetUpdateSchedule; the Dell lens realizes a ``JID_`` job id."""
    manager, service = dell_software_mock

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        payload_json='{"ShareName": "/repo"}',
        share_type="HTTPS",
        apply_reboot="RebootRequired",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.SetUpdateSchedule"
    assert result.data["target"] == SET_TARGET
    assert result.data["task_id"] == service.JOB_ID
    assert service.JOB_ID.startswith("JID_")
    assert len(posts) == 1
    assert posts[0].path.lower() == SET_TARGET.lower()
    assert posts[0].json() == {
        "ShareName": "/repo",
        "ShareType": "HTTPS",
        "ApplyReboot": "RebootRequired",
    }


def test_dell_software_update_schedule_set_dry_run_overrides_confirm(
    dell_software_mock,
):
    """--dry_run keeps SetUpdateSchedule from POSTing even with --confirm."""
    manager, service = dell_software_mock

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
    assert _post_requests(service) == []


def test_dell_software_update_schedule_rejects_invalid_allowable(
    dell_software_mock,
):
    """Inline allowable values reject invalid SetUpdateSchedule enum values."""
    manager, service = dell_software_mock

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
    assert _post_requests(service) == []


def test_dell_software_update_schedule_clear_previews(dell_software_mock):
    """ClearUpdateSchedule previews by default without POSTing."""
    manager, service = dell_software_mock

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
    assert _post_requests(service) == []


def test_dell_software_update_schedule_clear_confirm_posts_empty_payload(
    dell_software_mock,
):
    """--confirm POSTs an empty ClearUpdateSchedule body."""
    manager, service = dell_software_mock

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="clear",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#DellSoftwareInstallationService.ClearUpdateSchedule"
    assert result.data["target"] == CLEAR_TARGET
    assert len(posts) == 1
    assert posts[0].path.lower() == CLEAR_TARGET.lower()
    assert posts[0].json() == {}


def test_dell_software_update_schedule_reports_missing_action(
    dell_software_mock,
):
    """A service without SetUpdateSchedule reports the available schedule action."""
    manager, service = dell_software_mock
    body = copy.deepcopy(service._state(SERVICE))
    body["Actions"].pop("#DellSoftwareInstallationService.SetUpdateSchedule")
    _overlay_installation_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellSoftwareUpdateSchedule,
        "dell-software-update-schedule",
        action="set",
        share_type="HTTP",
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Dell software update schedule action not found: set"
    assert [row["Action"] for row in result.data["available"]] == ["clear"]
    assert _post_requests(service) == []


def test_dell_software_update_schedule_rejects_empty_set_payload(
    dell_software_mock,
):
    """SetUpdateSchedule requires at least one payload field."""
    manager, service = dell_software_mock

    with pytest.raises(InvalidArgument, match="--action set requires"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateSchedule,
            "dell-software-update-schedule",
            action="set",
        )

    assert _post_requests(service) == []


def test_dell_software_update_schedule_rejects_non_object_json(
    dell_software_mock,
):
    """The payload JSON must be an object."""
    manager, service = dell_software_mock

    with pytest.raises(InvalidArgument, match="JSON object"):
        manager.sync_invoke(
            ApiRequestType.DellSoftwareUpdateSchedule,
            "dell-software-update-schedule",
            action="set",
            payload_json='["not-object"]',
        )

    assert _post_requests(service) == []


def test_dell_software_update_schedule_is_registered():
    """The command is wired into the command registry."""
    registry = IDracManager().get_registry()
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
