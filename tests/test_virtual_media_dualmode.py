"""Dual-mode tests for virtual-media commands."""
import json

import pytest

from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType, IdracApiRespond
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


def test_virtual_media_query_prefers_dell_system_link_when_both_exist(
    redfish_mock, redfish_service
):
    """Dell-shaped resources keep the historical System VirtualMedia path."""
    manager_path = "/redfish/v1/Managers/iDRAC.Embedded.1"
    system_path = "/redfish/v1/Systems/System.Embedded.1"
    manager_state = dict(redfish_service._state(manager_path))
    system_state = dict(redfish_service._state(system_path))
    manager_state["VirtualMedia"] = {
        "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/VirtualMedia"
    }
    system_state["VirtualMedia"] = {
        "@odata.id": "/redfish/v1/Systems/System.Embedded.1/VirtualMedia"
    }
    for path, state in (
        (manager_path, manager_state),
        (manager_path.lower(), manager_state),
        (system_path, system_state),
        (system_path.lower(), system_state),
    ):
        redfish_service._overlay[path] = state

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualMediaGet, "virtual_disk_query"
    )

    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/VirtualMedia"
    )


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


def test_virtual_media_query_hydrates_manager_members(redfish_mock_factory):
    """Supermicro exposes VirtualMedia under the Manager and returns member links."""
    manager, _service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.VirtualMediaGet,
        "virtual_disk_query",
    )

    assert isinstance(result, CommandResult)
    assert result.data["@odata.id"] == "/redfish/v1/Managers/BMC_0/VirtualMedia"
    members = {member["Id"]: member for member in result.data["Members"]}
    assert {"USB1", "USB2", "Slot_0"} <= members.keys()
    assert members["USB1"]["Actions"]["#VirtualMedia.InsertMedia"]["target"] == (
        "/redfish/v1/Managers/BMC_0/VirtualMedia/USB1/"
        "Actions/VirtualMedia.InsertMedia"
    )


def test_virtual_media_discovery_reports_missing_roots(redfish_mock, monkeypatch):
    """Discovery raises the shared not-found exception when no root is available."""
    redfish_mock.__dict__["idrac_manage_servers"] = ""
    monkeypatch.setattr(redfish_mock, "discover_manager_ids", lambda: [])
    monkeypatch.setattr(redfish_mock, "discover_computer_system_ids", lambda: [])

    with pytest.raises(ResourceNotFound):
        redfish_mock.discover_virtual_media_uri()


@pytest.mark.parametrize(
    ("api_call", "name", "kwargs"),
    [
        (ApiRequestType.VirtualMediaGet, "virtual_disk_query", {}),
        (
            ApiRequestType.VirtualMediaInsert,
            "virtual_disk_insert",
            {"uri_path": "http://example.test/gb300.iso", "device_id": "USB1"},
        ),
        (
            ApiRequestType.VirtualMediaEject,
            "virtual_disk_eject",
            {"device_id": "USB1"},
        ),
    ],
)
def test_virtual_media_commands_report_missing_collection(
    redfish_mock, monkeypatch, api_call, name, kwargs
):
    """Commands report missing VirtualMedia as a command error, not a traceback."""
    monkeypatch.setattr(RedfishManagerBase, "idrac_manage_servers", property(lambda self: ""))
    monkeypatch.setattr(redfish_mock, "discover_manager_ids", lambda: [])
    monkeypatch.setattr(redfish_mock, "discover_computer_system_ids", lambda: [])
    monkeypatch.setattr(RedfishManagerBase, "discover_manager_ids", lambda self: [])
    monkeypatch.setattr(RedfishManagerBase, "discover_computer_system_ids", lambda self: [])

    result = redfish_mock.sync_invoke(api_call, name, **kwargs)

    assert isinstance(result, CommandResult)
    assert result.data == {
        "Status": "VirtualMedia collection not found in Managers or Systems"
    }
    assert result.error == "VirtualMedia collection not found in Managers or Systems"


def test_virtual_media_insert_uses_manager_action_target(
    redfish_mock_factory, monkeypatch
):
    """insert_vm uses the discovered Manager VirtualMedia action target."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setattr(
        RedfishManagerBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = manager.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "virtual_disk_insert",
        uri_path="http://example.test/gb300.iso",
        device_id="USB1",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == service.JOB_ID
    assert service.last_request.path == (
        "/redfish/v1/managers/bmc_0/virtualmedia/usb1/"
        "actions/virtualmedia.insertmedia"
    )
    assert service.last_request.json() == {
        "Image": "http://example.test/gb300.iso",
        "Inserted": True,
        "WriteProtected": True,
    }


def test_virtual_media_eject_uses_hydrated_manager_action_target(
    redfish_mock_factory, monkeypatch
):
    """eject_vm uses hydrated member links from the Manager VirtualMedia collection."""
    manager, service = redfish_mock_factory("supermicro")
    device_path = "/redfish/v1/Managers/BMC_0/VirtualMedia/USB1"
    device_state = dict(service._state(device_path))
    device_state["Inserted"] = True
    device_state["Image"] = "http://example.test/gb300.iso"
    service._overlay[device_path] = device_state
    service._overlay[device_path.lower()] = device_state
    monkeypatch.setattr(
        RedfishManagerBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = manager.sync_invoke(
        ApiRequestType.VirtualMediaEject,
        "virtual_disk_eject",
        device_id="USB1",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == service.JOB_ID
    assert service.last_request.path == (
        "/redfish/v1/managers/bmc_0/virtualmedia/usb1/"
        "actions/virtualmedia.ejectmedia"
    )
    assert service.last_request.json() == {}


def test_virtual_media_insert_posts_action_payload(
    redfish_mock, redfish_service, monkeypatch
):
    """virtual_disk_insert POSTs to the member InsertMedia action target."""
    monkeypatch.setattr(
        RedfishManagerBase,
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
        RedfishManagerBase,
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
