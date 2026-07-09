"""Dual-mode tests for virtual-media commands."""
import json

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType, IdracApiRespond
from redfish_ctl.redfish_manager import CommandResult


def test_virtual_media_query_returns_collection(redfish_api):
    """virtual_disk_query returns the expanded virtual-media collection."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaGet, "virtual_disk_query"
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/VirtualMedia"
    )
    assert result.data["Members@odata.count"] == 2
    assert [member["Id"] for member in result.data["Members"]] == ["1", "2"]


def test_virtual_media_query_filters_by_device_id(redfish_api):
    """device_id returns the matching virtual-media member."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaGet,
        "virtual_disk_query",
        device_id="2",
    )

    assert isinstance(result, CommandResult)
    assert result.data["Id"] == "2"
    assert result.data["Inserted"] is True
    assert result.data["Image"] == "http://example.test/installer.iso"


def test_virtual_media_query_filter_key_returns_member_value(redfish_api):
    """filter_key narrows a device response to one field."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaGet,
        "virtual_disk_query",
        device_id="2",
        filter_key="Image",
    )

    assert isinstance(result, CommandResult)
    assert result.data == "http://example.test/installer.iso"


def test_virtual_media_query_filter_key_reports_missing_key(redfish_api):
    """filter_key returns a status payload when the requested field is absent."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaGet,
        "virtual_disk_query",
        device_id="1",
        filter_key="MissingField",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": "key MissingField not found"}


def test_virtual_media_query_reports_missing_device(redfish_api):
    """unknown device_id returns a status payload instead of a member."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaGet,
        "virtual_disk_query",
        device_id="99",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": "device id 99 not found"}


def test_virtual_media_insert_posts_action_payload(
    redfish_mock, redfish_service, monkeypatch
):
    """virtual_disk_insert POSTs to the member InsertMedia action target."""
    monkeypatch.setattr(
        IDracManager,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "virtual_disk_insert",
        uri_path="http://example.test/new.iso",
        device_id="1",
        remote_username="media-user",
        remote_password="media-pass",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/systems/system.embedded.1/virtualmedia/1/"
        "actions/virtualmedia.insertmedia"
    )
    assert redfish_service.last_request.json() == {
        "Image": "http://example.test/new.iso",
        "Inserted": True,
        "WriteProtected": True,
        "UserName": "media-user",
        "Password": "media-pass",
    }


def test_virtual_media_eject_posts_action_payload(
    redfish_mock, redfish_service, monkeypatch
):
    """virtual_disk_eject POSTs an empty body to the member EjectMedia target."""
    monkeypatch.setattr(
        IDracManager,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualMediaEject,
        "virtual_disk_eject",
        device_id="2",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/systems/system.embedded.1/virtualmedia/2/"
        "actions/virtualmedia.ejectmedia"
    )
    assert redfish_service.last_request.json() == {}


def test_virtual_media_eject_skips_post_when_device_is_already_empty(
    redfish_mock, redfish_service
):
    """non-strict eject returns Ok without POSTing when media is not inserted."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualMediaEject,
        "virtual_disk_eject",
        device_id="1",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"Status": IdracApiRespond.Ok}
    assert redfish_service.last_request.method == "GET"
