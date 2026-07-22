"""Dual-mode tests for boot option and boot-source commands."""
import json

import pytest
import requests

from redfish_ctl.boot_source.cmd_clear_pending import BootOptionsClearPending
from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_boot_options_list_returns_member_uris(redfish_api):
    """boot_sources_query returns BootOptions member Redfish URIs."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootOptions,
        "boot_sources_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.data == [
        "/redfish/v1/Systems/System.Embedded.1/BootOptions/HardDisk.List.1-1",
        "/redfish/v1/Systems/System.Embedded.1/BootOptions/NIC.PxeDevice.1-1",
    ]
    assert result.extra["Members@odata.count"] == 2


def test_boot_options_list_404_non_json_raises_resource_not_found(
        redfish_api, monkeypatch):
    """Plain-text 404 BootOptions responses raise cleanly without JSON traceback."""
    original_api_get_call = IDracManager.api_get_call

    def api_get_call(self, request, headers=None):
        """Return the X10-style non-JSON BootOptions failure.

        :param self: command instance issuing the GET.
        :param request: BootOptions collection request URL.
        :param headers: optional HTTP headers sent by the command.
        :return: a plain-text 404 response.
        """
        if "/BootOptions" not in request:
            return original_api_get_call(self, request, headers)
        response = requests.Response()
        response.status_code = 404
        response._content = b"BootOptions not available"
        response.headers["Content-Type"] = "text/plain"
        return response

    monkeypatch.setattr(IDracManager, "api_get_call", api_get_call)

    with pytest.raises(ResourceNotFound):
        redfish_api.sync_invoke(
            ApiRequestType.BootOptions,
            "boot_sources_query",
        )


def test_boot_options_query_returns_collection(redfish_api):
    """boot_options_query returns the BootOptions collection resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootOptionQuery,
        "boot_options_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/BootOptions"
    )
    assert result.data["Members"][0]["@odata.id"].endswith("/HardDisk.List.1-1")


def test_boot_source_query_filters_linked_boot_option(redfish_api):
    """boot_source_query follows BootOptions links and returns the requested device."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryBootOption,
        "boot_source_query",
        boot_source="NIC.PxeDevice",
    )

    assert isinstance(result, CommandResult)
    assert list(result.data) == ["NIC.PxeDevice.1-1"]
    option = result.data["NIC.PxeDevice.1-1"]
    json.dumps(option)
    assert option["BootOptionReference"] == "NIC.PxeDevice.1-1"
    assert option["UefiDevicePath"].startswith("PciRoot")


def test_boot_source_pending_unwraps_settings_attributes(redfish_api):
    """query_pending unwraps DellBootSources Settings Attributes."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootSourcePending,
        "query_pending",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    assert result.data["BootMode"] == "Uefi"
    assert result.data["UefiBootSeq"][0]["Name"] == "NIC.PxeDevice.1-1"


def test_boot_source_pending_filter_returns_named_attribute(redfish_api):
    """query_pending returns a selected DellBootSources Settings attribute."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootSourcePending,
        "query_pending",
        data_filter="BootMode",
    )

    assert isinstance(result, CommandResult)
    assert result.data == "Uefi"


def test_boot_settings_query_returns_dell_boot_sources_settings(redfish_api):
    """boot_settings_query returns the DellBootSources Settings resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootSettingsQuery,
        "boot_settings_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellBootSources/Settings"
    )
    assert result.data["Attributes"]["BootMode"] == "Uefi"
    assert result.data["Attributes"]["UefiBootSeq"][0]["Name"] == "NIC.PxeDevice.1-1"
    assert result.discovered["ClearPending"].target == (
        "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellBootSources/Settings/"
        "Actions/DellManager.ClearPending"
    )


def test_boot_options_clear_posts_clear_pending_action_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """boot-options-clear POSTs an empty payload to the Dell clear-pending action."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(BootOptionsClearPending, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.BootOptionsClearPending,
        "clear_pending",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/oem/dell/dellbootsources/settings/"
        "actions/dellmanager.clearpending"
    )
    assert request.json() == {}
