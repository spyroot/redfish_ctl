"""Dual-mode regression tests for BootOptions commands.

    redfish_ctl boot-sources
    redfish_ctl boot-options
"""
import json

import pytest
import requests

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
