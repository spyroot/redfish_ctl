"""Dual-mode tests for the ntp-set command."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def test_ntp_set_dry_run_builds_gb300_patch_plan_without_writing(
        redfish_mock_factory):
    """ntp-set previews NTP-only ManagerNetworkProtocol PATCHes by default."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org", "1.pool.ntp.org"],
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["servers"] == ["0.pool.ntp.org", "1.pool.ntp.org"]
    assert result.data["skipped"] == [{
        "Manager": "HGX_BMC_0",
        "target": "/redfish/v1/Managers/HGX_BMC_0/NetworkProtocol",
        "reason": "NTP block is not available",
    }]
    assert result.data["plan"] == [{
        "Manager": "BMC_0",
        "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
        "payload": {
            "NTP": {
                "NTPServers": ["0.pool.ntp.org", "1.pool.ntp.org"],
                "ProtocolEnabled": True,
            },
        },
    }]
    assert _mutating_requests(service) == []


def test_ntp_set_confirm_patches_only_the_ntp_block(redfish_mock_factory):
    """ntp-set --confirm PATCHes NTP servers without changing other protocols."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org"],
        confirm=True,
    )

    patches = [request for request in service.requests if request.method == "PATCH"]
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/networkprotocol"
    assert patches[0].json() == {
        "NTP": {
            "NTPServers": ["0.pool.ntp.org"],
            "ProtocolEnabled": True,
        },
    }
    assert set(patches[0].json()) == {"NTP"}
    assert result.data["applied"] == [{
        "Manager": "BMC_0",
        "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
        "status": "IdracApiRespond.Ok",
        "error": None,
    }]


def test_ntp_set_clear_patches_empty_server_list_only(redfish_mock_factory):
    """ntp-set --clear restores an empty NTP server list without toggling NTP."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        clear=True,
        confirm=True,
    )

    patches = [request for request in service.requests if request.method == "PATCH"]
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/networkprotocol"
    assert patches[0].json() == {"NTP": {"NTPServers": []}}
    assert result.data["servers"] == []
    assert result.data["applied"] == [{
        "Manager": "BMC_0",
        "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
        "status": "IdracApiRespond.Ok",
        "error": None,
    }]


def test_ntp_set_clear_rejects_explicit_servers(redfish_mock_factory):
    """ntp-set --clear is mutually exclusive with explicit server values."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.NtpSet,
            "ntp-set",
            servers=["0.pool.ntp.org"],
            clear=True,
            confirm=True,
        )

    assert _mutating_requests(service) == []


@pytest.mark.parametrize(
    "servers",
    [
        ["https://0.pool.ntp.org"],
        ["bad host"],
        ["ntp_underscore.example"],
        ["0.pool.ntp.org", "1.pool.ntp.org", "2.pool.ntp.org",
         "3.pool.ntp.org", "4.pool.ntp.org"],
    ],
)
def test_ntp_set_rejects_invalid_server_lists_before_patch(
        redfish_mock_factory, servers):
    """ntp-set validates server names and the Redfish four-server limit first."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.NtpSet,
            "ntp-set",
            servers=servers,
            confirm=True,
        )

    assert _mutating_requests(service) == []
