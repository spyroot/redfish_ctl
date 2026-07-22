"""Dual-mode tests for the dns-set command."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType


def _patch_requests(service):
    return [request for request in service.requests if request.method == "PATCH"]


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def _overlay_single_manager_with_eth0(service):
    """Pin discovery to one Manager (BMC_0) with a single eth0 EthernetInterface."""
    service._overlay["/redfish/v1/managers"] = {
        "@odata.id": "/redfish/v1/Managers",
        "Members": [{"@odata.id": "/redfish/v1/Managers/BMC_0"}],
        "Members@odata.count": 1,
    }
    service._overlay["/redfish/v1/managers/bmc_0"] = {
        "@odata.id": "/redfish/v1/Managers/BMC_0",
        "Id": "BMC_0",
        "EthernetInterfaces": {
            "@odata.id": "/redfish/v1/Managers/BMC_0/EthernetInterfaces",
        },
    }
    service._overlay["/redfish/v1/managers/bmc_0/ethernetinterfaces"] = {
        "@odata.id": "/redfish/v1/Managers/BMC_0/EthernetInterfaces",
        "Members": [
            {"@odata.id": "/redfish/v1/Managers/BMC_0/EthernetInterfaces/eth0"},
        ],
        "Members@odata.count": 1,
    }
    service._overlay["/redfish/v1/managers/bmc_0/ethernetinterfaces/eth0"] = {
        "@odata.id": "/redfish/v1/Managers/BMC_0/EthernetInterfaces/eth0",
        "Id": "eth0",
        "StaticNameServers": [],
    }


def test_dns_set_dry_run_previews_static_name_servers_without_writing(
        redfish_mock_factory):
    """dns-set previews the StaticNameServers PATCH target by default."""
    manager, service = redfish_mock_factory("supermicro")
    _overlay_single_manager_with_eth0(service)

    result = manager.sync_invoke(
        ApiRequestType.DnsSet, "dns-set",
        servers=["8.8.8.8"], interface_id="eth0")

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["servers"] == ["8.8.8.8"]
    assert result.data["targets"] == [
        "/redfish/v1/Managers/BMC_0/EthernetInterfaces/eth0"]
    assert _mutating_requests(service) == []


def test_dns_set_confirm_patches_static_name_servers(redfish_mock_factory):
    """dns-set --confirm PATCHes StaticNameServers on the EthernetInterface."""
    manager, service = redfish_mock_factory("supermicro")
    _overlay_single_manager_with_eth0(service)

    result = manager.sync_invoke(
        ApiRequestType.DnsSet, "dns-set",
        servers=["8.8.8.8"], interface_id="eth0", confirm=True)

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/ethernetinterfaces/eth0"
    assert patches[0].json() == {"StaticNameServers": ["8.8.8.8"]}
    assert result.data["applied"] == [{
        "target": "/redfish/v1/Managers/BMC_0/EthernetInterfaces/eth0",
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }]


def test_dns_set_clear_patches_empty_static_name_servers(redfish_mock_factory):
    """dns-set --clear PATCHes an empty StaticNameServers list."""
    manager, service = redfish_mock_factory("supermicro")
    _overlay_single_manager_with_eth0(service)

    result = manager.sync_invoke(
        ApiRequestType.DnsSet, "dns-set",
        clear=True, interface_id="eth0", confirm=True)

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].json() == {"StaticNameServers": []}
    assert result.data["servers"] == []


def test_dns_set_clear_rejects_explicit_servers(redfish_mock_factory):
    """dns-set --clear is mutually exclusive with explicit --server values."""
    manager, service = redfish_mock_factory("supermicro")
    _overlay_single_manager_with_eth0(service)

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.DnsSet, "dns-set",
            servers=["8.8.8.8"], clear=True, interface_id="eth0", confirm=True)

    assert _mutating_requests(service) == []
