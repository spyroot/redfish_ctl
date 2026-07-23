"""Dual-mode tests for the boot-options query command (BootOptionsQuery).

    redfish_ctl boot-options

Covers ``boot_options_query`` (ApiRequestType.BootOptionQuery), a read-only DMTF
BootOptionCollection GET via the shared ``base_query`` helper. Runs offline
against the mock service by default and against real hardware when IDRAC_IP is set.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_boot_options_query_returns_collection(redfish_api):
    """boot_options_query returns the BootOptionCollection body unmodified."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BootOptionQuery,
        "boot_options_query",
    )
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    assert result.data["@odata.type"] == "#BootOptionCollection.BootOptionCollection"
    assert result.data["Members@odata.count"] == 2
    assert result.error is None


def test_boot_options_query_targets_bootoptions_collection(redfish_mock, redfish_service):
    """boot_options_query GETs the ComputerSystem BootOptions collection path."""
    redfish_mock.sync_invoke(ApiRequestType.BootOptionQuery, "boot_options_query")
    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower().endswith("/bootoptions")
