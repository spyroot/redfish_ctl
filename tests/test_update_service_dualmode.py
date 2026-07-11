"""Dual-mode tests for the read-only UpdateService command."""
import json
import subprocess
import sys

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"


def _assert_read_only(service):
    assert {
        request.method
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()


def test_update_service_query_reports_standard_simple_update(redfish_api):
    """update_service reports standard SimpleUpdate action metadata."""
    result = redfish_api.sync_invoke(
        ApiRequestType.UpdateServiceQuery,
        "update_service",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data, sort_keys=True)
    assert result.data["@odata.id"] == UPDATE_SERVICE_PATH
    assert result.data["Id"] == "UpdateService"
    assert result.data["ServiceEnabled"] is True
    assert result.data["FirmwareInventory"] == (
        "/redfish/v1/UpdateService/FirmwareInventory"
    )

    simple_update = {
        action["FullName"]: action for action in result.data["Actions"]
    }["#UpdateService.SimpleUpdate"]
    assert simple_update["Name"] == "SimpleUpdate"
    assert simple_update["Target"].endswith(
        "/UpdateService/Actions/UpdateService.SimpleUpdate"
    )
    assert simple_update["Parameters"]["TransferProtocol"] == ["HTTP", "HTTPS"]


def test_update_service_query_reports_oem_push_paths_without_writes(
    redfish_mock_factory,
):
    """update_service reports GB300 push URIs and nested OEM actions read-only."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.UpdateServiceQuery,
        "update_service",
    )

    assert isinstance(result, CommandResult)
    assert result.data["@odata.id"] == UPDATE_SERVICE_PATH
    assert result.data["HttpPushUri"] == "/redfish/v1/UpdateService/update"
    assert result.data["MultipartHttpPushUri"] == (
        "/redfish/v1/UpdateService/update-multipart"
    )
    assert result.data["SoftwareInventory"] == (
        "/redfish/v1/UpdateService/SoftwareInventory"
    )

    actions = {action["FullName"]: action for action in result.data["Actions"]}
    assert "#UpdateService.SimpleUpdate" not in actions
    assert actions["#NvidiaUpdateService.CommitImage"]["Target"].endswith(
        "/Actions/Oem/NvidiaUpdateService.CommitImage"
    )
    assert actions["#NvidiaUpdateService.PublicKeyExchange"]["Name"] == (
        "PublicKeyExchange"
    )
    assert all(action["Target"] for action in actions.values())
    assert service.last_request.method == "GET"
    assert service.last_request.path.lower() == UPDATE_SERVICE_PATH.lower()
    _assert_read_only(service)


def test_update_service_help_is_registered():
    """update_service --help builds the CLI parser without option conflicts."""
    result = subprocess.run(
        [sys.executable, "-m", "redfish_ctl", "update_service", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "update_service" in result.stdout
    assert "--filename" in result.stdout
