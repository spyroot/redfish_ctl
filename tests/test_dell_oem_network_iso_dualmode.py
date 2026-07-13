"""Dual-mode tests for Dell OEM network ISO commands."""

import json

import pytest

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

OS_DEPLOYMENT = "/redfish/v1/Dell/Systems/System.Embedded.1/DellOSDeploymentService"
ACTION_PREFIX = f"{OS_DEPLOYMENT}/Actions/DellOSDeploymentService"
OS_DEPLOYMENT_TASK = "/redfish/v1/TaskService/Tasks/OSDeployment"


def test_dell_oem_actions_discovers_network_iso_actions(redfish_api):
    """dell_oem_actions exposes the Dell OS deployment action targets."""
    result = redfish_api.sync_invoke(
        ApiRequestType.DellOemActions,
        "dell_oem_actions",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    json.dumps(result.data.data)
    assert result.data.data["@odata.id"] == OS_DEPLOYMENT
    assert {
        "ConnectNetworkISOImage",
        "DetachISOImage",
        "DisconnectNetworkISOImage",
        "GetAttachStatus",
        "GetNetworkISOImageConnectionInfo",
        "BootToNetworkISO",
    }.issubset(result.discovered)
    assert result.discovered["ConnectNetworkISOImage"].target == (
        f"{ACTION_PREFIX}.ConnectNetworkISOImage"
    )


def test_oem_net_iso_task_discovers_os_deployment_task_actions(
    redfish_mock,
    redfish_service,
):
    """oem-net-iso-task reads the OSDeployment task and discovers its actions."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOemTask,
        "dell_oem_actions",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, CommandResult)
    assert isinstance(result.data.data, dict)
    assert result.data.data["@odata.id"] == OS_DEPLOYMENT_TASK
    assert "Cancel" in result.discovered
    assert result.discovered["Cancel"].target == (
        f"{OS_DEPLOYMENT_TASK}/Actions/Task.Cancel"
    )

    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower() == OS_DEPLOYMENT_TASK.lower()


def test_dell_oem_attach_posts_network_iso_payload(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """delloem_attach POSTs the network ISO payload to the discovered action."""
    monkeypatch.setattr(
        CommandBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.OemAttach,
        "delloem_attach",
        ip_addr="192.0.2.10",
        share_type="NFS",
        share_name="/exports/isos",
        remote_image="rhel.iso",
        remote_username="media-user",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        "actions/dellosdeploymentservice.connectnetworkisoimage"
    )
    assert redfish_service.last_request.json() == {
        "IPAddress": "192.0.2.10",
        "ShareType": "NFS",
        "ShareName": "/exports/isos",
        "ImageName": "rhel.iso",
        "UserName": "media-user",
    }


def test_dell_oem_boot_network_iso_posts_discovered_action_payload(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """oem-boot-netios POSTs share details to BootToNetworkISO."""
    monkeypatch.setattr(
        CommandBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOemNetIsoBoot,
        "delloem_netios_boot",
        ip_addr="192.0.2.20",
        share_type="CIFS",
        share_name="install-media",
        remote_image="ubuntu.iso",
        remote_username="media-user",
        remote_workgroup="LAB",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        "actions/dellosdeploymentservice.boottonetworkiso"
    )
    assert redfish_service.last_request.json() == {
        "IPAddress": "192.0.2.20",
        "ShareType": "CIFS",
        "ShareName": "install-media",
        "ImageName": "ubuntu.iso",
        "UserName": "media-user",
        "Workgroup": "LAB",
    }


def test_dell_oem_attach_status_posts_empty_body_to_get_attach_status(
    redfish_mock,
    redfish_service,
):
    """oem-attach-status POSTs an empty body to the discovered GetAttachStatus action."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.GetAttachStatus,
        "get_attach_status",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {}
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert redfish_service.last_request.method == "POST"
    assert redfish_service.last_request.path == (
        "/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        "actions/dellosdeploymentservice.getattachstatus"
    )
    assert redfish_service.last_request.json() == {}


@pytest.mark.parametrize(
    ("request_type", "command_name", "action_name"),
    [
        (
            ApiRequestType.GetNetworkIsoAttachStatus,
            "net_ios_attach_status",
            "GetNetworkISOImageConnectionInfo",
        ),
        (
            ApiRequestType.DellOemDetach,
            "delloem_detach",
            "DetachISOImage",
        ),
        (
            ApiRequestType.DellOemDisconnect,
            "delloem_disconnect",
            "DisconnectNetworkISOImage",
        ),
    ],
)
def test_dell_oem_empty_action_posts_use_discovered_targets(
    redfish_mock,
    redfish_service,
    monkeypatch,
    request_type,
    command_name,
    action_name,
):
    """Dell OEM empty-body commands POST to their discovered action targets."""
    monkeypatch.setattr(
        CommandBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(request_type, command_name)

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.last_request.path == (
        f"/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        f"actions/dellosdeploymentservice.{action_name.lower()}"
    )
    assert redfish_service.last_request.json() == {}


def test_dell_oem_attach_status_posts_discovered_status_action(
    redfish_mock,
    redfish_service,
):
    """oem-attach-status POSTs to the discovered GetAttachStatus target."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.GetAttachStatus,
        "get_attach_status",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {}
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    assert redfish_service.last_request.method == "POST"
    assert redfish_service.last_request.path == (
        "/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        "actions/dellosdeploymentservice.getattachstatus"
    )
    assert redfish_service.last_request.json() == {}


def test_dell_oem_network_iso_boot_posts_network_iso_payload(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """delloem_netios_boot POSTs the network ISO payload to BootToNetworkISO."""
    monkeypatch.setattr(
        CommandBase,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.DellOemNetIsoBoot,
        "delloem_netios_boot",
        ip_addr="192.0.2.20",
        share_type="CIFS",
        share_name="//192.0.2.20/isos",
        remote_image="rocky.iso",
        remote_username="media-user",
        remote_password="mock-password",
        remote_workgroup="lab",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/dell/systems/system.embedded.1/dellosdeploymentservice/"
        "actions/dellosdeploymentservice.boottonetworkiso"
    )
    assert redfish_service.last_request.json() == {
        "IPAddress": "192.0.2.20",
        "ShareType": "CIFS",
        "ShareName": "//192.0.2.20/isos",
        "ImageName": "rocky.iso",
        "UserName": "media-user",
        "Password": "mock-password",
        "Workgroup": "lab",
    }
