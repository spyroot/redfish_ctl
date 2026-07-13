"""Dual-mode tests for the compute settings command."""
import json

import pytest

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.compute.cmd_update import UpdateCompute  # noqa: F401
from redfish_ctl.redfish_manager import CommandResult


def test_compute_query_returns_system_settings_for_610_plus_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """compute-query returns the ComputerSystem Settings resource on iDRAC 6.10+."""
    monkeypatch.setattr(
        CommandBase,
        "base_manager_version",
        property(lambda self: "6.10.00.00"),
    )

    result = redfish_mock.sync_invoke(ApiRequestType.ComputeQuery, "query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/Settings"
    )
    assert result.data["@odata.type"].startswith("#ComputerSystem.")
    assert result.data["Id"] == "Settings"
    assert "Boot" in result.data
    assert redfish_service.last_request.method == "GET"
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/settings"
    )


def test_compute_query_uses_system_resource_before_610(
    redfish_api, redfish_service, monkeypatch
):
    """compute-query reads the ComputerSystem resource before the Settings URI is available."""
    monkeypatch.setattr(
        CommandBase,
        "base_manager_version",
        property(lambda self: "6.00.00.00"),
    )

    result = redfish_api.sync_invoke(ApiRequestType.ComputeQuery, "query")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1"
    assert result.data["Id"] == "System.Embedded.1"
    assert result.error is None
    assert redfish_service.last_request.method == "GET"
    assert (
        redfish_service.last_request.path.lower()
        == "/redfish/v1/systems/system.embedded.1"
    )


def test_compute_update_uses_system_resource_before_610_without_mutating(
    redfish_api, redfish_service, monkeypatch
):
    """compute-update reads the ComputerSystem resource before the Settings URI is available."""
    monkeypatch.setattr(
        CommandBase,
        "base_manager_version",
        property(lambda self: "6.00.00.00"),
    )
    request_count = len(redfish_service.requests)

    result = redfish_api.sync_invoke(ApiRequestType.ComputeUpdate, "update")
    new_requests = redfish_service.requests[request_count:]

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1"
    assert result.data["Id"] == "System.Embedded.1"
    assert result.error is None
    assert redfish_service.last_request.method == "GET"
    assert (
        redfish_service.last_request.path.lower()
        == "/redfish/v1/systems/system.embedded.1"
    )
    assert new_requests
    assert {request.method for request in new_requests} == {"GET"}


@pytest.mark.parametrize("manager_version", ["7.00.00.00", "8.00.00.00"])
def test_compute_update_uses_settings_resource_after_610_without_mutating(
    redfish_api, redfish_service, monkeypatch, manager_version
):
    """compute-update reads the ComputerSystem Settings resource on iDRAC 7.x and 8.x."""
    monkeypatch.setattr(
        CommandBase,
        "base_manager_version",
        property(lambda self: manager_version),
    )
    request_count = len(redfish_service.requests)

    result = redfish_api.sync_invoke(ApiRequestType.ComputeUpdate, "update")
    new_requests = redfish_service.requests[request_count:]

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Systems/System.Embedded.1/Settings"
    )
    assert result.data["@odata.type"].startswith("#ComputerSystem.")
    assert result.data["Id"] == "Settings"
    assert result.error is None
    assert redfish_service.last_request.method == "GET"
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1/settings"
    )
    assert new_requests
    assert {request.method for request in new_requests} == {"GET"}




def test_compute_update_reads_system_resource_for_pre_610_idrac(
    redfish_mock, redfish_service, monkeypatch
):
    """compute-update reads the ComputerSystem resource before iDRAC 6.10."""
    monkeypatch.setattr(
        CommandBase,
        "base_manager_version",
        property(lambda self: "6.00.00.00"),
    )

    result = redfish_mock.sync_invoke(ApiRequestType.ComputeUpdate, "update")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1"
    assert result.data["Id"] == "System.Embedded.1"
    assert redfish_service.last_request.method == "GET"
    assert redfish_service.last_request.path.lower() == (
        "/redfish/v1/systems/system.embedded.1"
    )
    assert {
        request.method
        for request in redfish_service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()
