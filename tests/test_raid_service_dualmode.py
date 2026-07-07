"""Dual-mode tests for the RAID service command."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_raid_service_query_returns_service_actions(redfish_api):
    """raid_service_query returns Dell RAID service data and all action targets."""
    result = redfish_api.sync_invoke(
        ApiRequestType.RaidServiceQuery,
        "raid_service_query",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService"
    )
    assert result.data["Id"] == "DellRaidService"
    assert result.discovered == {
        "AssignSpare": (
            "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService/"
            "Actions/DellRaidService.AssignSpare"
        ),
        "ConvertToNonRAID": (
            "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService/"
            "Actions/DellRaidService.ConvertToNonRAID"
        ),
        "ConvertToRAID": (
            "/redfish/v1/Dell/Systems/System.Embedded.1/DellRaidService/"
            "Actions/DellRaidService.ConvertToRAID"
        ),
    }
