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


def _patch_requests(service):
    return [request for request in service.requests if request.method == "PATCH"]


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


def test_ntp_set_dry_run_builds_x10_legacy_ntp_patch_plan(redfish_mock_factory):
    """ntp-set previews Supermicro X10 Manager NTP PATCHes by default."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org", "1.pool.ntp.org"],
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["servers"] == ["0.pool.ntp.org", "1.pool.ntp.org"]
    assert result.data["skipped"] == []
    assert result.data["plan"] == [{
        "Manager": "1",
        "target": "/redfish/v1/Managers/1/NTP",
        "payload": {
            "NTPEnable": True,
            "PrimaryNTPServer": "0.pool.ntp.org",
            "SecondaryNTPServer": "1.pool.ntp.org",
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
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }]


def test_ntp_set_confirm_patches_x10_legacy_ntp_resource(redfish_mock_factory):
    """ntp-set --confirm PATCHes Supermicro X10 Manager NTP fields."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org"],
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/1/ntp"
    assert patches[0].json() == {
        "NTPEnable": True,
        "PrimaryNTPServer": "0.pool.ntp.org",
        "SecondaryNTPServer": "",
    }
    assert result.data["applied"] == [{
        "Manager": "1",
        "target": "/redfish/v1/Managers/1/NTP",
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }]


def test_ntp_set_clear_patches_x10_legacy_ntp_resource(redfish_mock_factory):
    """ntp-set --clear PATCHes empty Supermicro X10 Manager NTP fields."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        clear=True,
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/1/ntp"
    assert patches[0].json() == {
        "NTPEnable": False,
        "PrimaryNTPServer": "",
        "SecondaryNTPServer": "",
    }
    assert result.data["servers"] == []
    assert result.data["applied"] == [{
        "Manager": "1",
        "target": "/redfish/v1/Managers/1/NTP",
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }]


def test_ntp_set_skips_x10_legacy_target_when_server_list_too_long(
        redfish_mock_factory):
    """ntp-set still patches standard managers when legacy targets cannot fit."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/managers"] = {
        "@odata.id": "/redfish/v1/Managers",
        "Members": [
            {"@odata.id": "/redfish/v1/Managers/BMC_0"},
            {"@odata.id": "/redfish/v1/Managers/legacy"},
        ],
        "Members@odata.count": 2,
    }
    service._overlay["/redfish/v1/managers/legacy"] = {
        "@odata.id": "/redfish/v1/Managers/legacy",
        "Id": "legacy",
        "Oem": {
            "Supermicro": {
                "NTP": {"@odata.id": "/redfish/v1/Managers/legacy/NTP"},
            },
        },
    }
    service._overlay["/redfish/v1/managers/legacy/ntp"] = {
        "@odata.id": "/redfish/v1/Managers/legacy/NTP",
        "Id": "NTP",
        "NTPEnable": False,
        "PrimaryNTPServer": "",
        "SecondaryNTPServer": "",
    }

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org", "1.pool.ntp.org", "2.pool.ntp.org"],
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/networkprotocol"
    assert patches[0].json() == {
        "NTP": {
            "NTPServers": [
                "0.pool.ntp.org",
                "1.pool.ntp.org",
                "2.pool.ntp.org",
            ],
            "ProtocolEnabled": True,
        },
    }
    assert result.data["applied"] == [{
        "Manager": "BMC_0",
        "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }]
    assert result.data["skipped"] == [{
        "Manager": "legacy",
        "target": "/redfish/v1/Managers/legacy/NTP",
        "reason": "legacy Manager NTP resources support at most 2 servers",
    }]


def test_ntp_set_ignores_conventional_ntp_path_for_non_supermicro_manager(
        redfish_mock_factory):
    """ntp-set only uses the conventional Manager NTP path for Supermicro OEMs."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/managers"] = {
        "@odata.id": "/redfish/v1/Managers",
        "Members": [
            {"@odata.id": "/redfish/v1/Managers/BMC_0"},
            {"@odata.id": "/redfish/v1/Managers/vendor"},
        ],
        "Members@odata.count": 2,
    }
    service._overlay["/redfish/v1/managers/vendor"] = {
        "@odata.id": "/redfish/v1/Managers/vendor",
        "Id": "vendor",
        "NetworkProtocol": {
            "@odata.id": "/redfish/v1/Managers/vendor/NetworkProtocol",
        },
        "Oem": {
            "OtherVendor": {
                "NTP": {"@odata.id": "/redfish/v1/Managers/vendor/NTP"},
            },
        },
    }
    service._overlay["/redfish/v1/managers/vendor/networkprotocol"] = {
        "@odata.id": "/redfish/v1/Managers/vendor/NetworkProtocol",
        "Id": "NetworkProtocol",
    }
    service._overlay["/redfish/v1/managers/vendor/ntp"] = {
        "@odata.id": "/redfish/v1/Managers/vendor/NTP",
        "Id": "NTP",
        "NTPEnable": False,
        "PrimaryNTPServer": "",
        "SecondaryNTPServer": "",
    }

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org"],
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/networkprotocol"
    assert "/redfish/v1/managers/vendor/ntp" not in {
        request.path for request in service.requests
    }
    assert result.data["skipped"] == [{
        "Manager": "vendor",
        "target": "/redfish/v1/Managers/vendor/NetworkProtocol",
        "reason": "NTP block is not available",
    }]


def test_ntp_set_does_not_fallback_when_network_protocol_is_unreadable(
        redfish_mock_factory):
    """ntp-set skips legacy fallback when standard NetworkProtocol cannot be read."""
    manager, service = redfish_mock_factory("supermicro")
    service._overlay["/redfish/v1/managers"] = {
        "@odata.id": "/redfish/v1/Managers",
        "Members": [
            {"@odata.id": "/redfish/v1/Managers/BMC_0"},
            {"@odata.id": "/redfish/v1/Managers/broken"},
        ],
        "Members@odata.count": 2,
    }
    service._overlay["/redfish/v1/managers/broken"] = {
        "@odata.id": "/redfish/v1/Managers/broken",
        "Id": "broken",
        "NetworkProtocol": {
            "@odata.id": "/redfish/v1/Managers/broken/NetworkProtocol",
        },
        "Oem": {
            "Supermicro": {
                "NTP": {"@odata.id": "/redfish/v1/Managers/broken/NTP"},
            },
        },
    }
    service._overlay["/redfish/v1/managers/broken/ntp"] = {
        "@odata.id": "/redfish/v1/Managers/broken/NTP",
        "Id": "NTP",
        "NTPEnable": False,
        "PrimaryNTPServer": "",
        "SecondaryNTPServer": "",
    }

    result = manager.sync_invoke(
        ApiRequestType.NtpSet,
        "ntp-set",
        servers=["0.pool.ntp.org"],
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path == "/redfish/v1/managers/bmc_0/networkprotocol"
    assert "/redfish/v1/managers/broken/ntp" not in {
        request.path for request in service.requests
    }
    assert result.data["skipped"] == [{
        "Manager": "broken",
        "target": "/redfish/v1/Managers/broken/NetworkProtocol",
        "reason": "NetworkProtocol resource is not readable",
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
        "status": "RedfishApiRespond.Ok",
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


def test_ntp_set_rejects_too_many_x10_legacy_servers_before_patch(
        redfish_mock_factory):
    """Supermicro X10 legacy NTP resources reject a third server."""
    manager, service = redfish_mock_factory("supermicro_x10")

    with pytest.raises(InvalidArgument, match="legacy Manager NTP"):
        manager.sync_invoke(
            ApiRequestType.NtpSet,
            "ntp-set",
            servers=["0.pool.ntp.org", "1.pool.ntp.org", "2.pool.ntp.org"],
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
