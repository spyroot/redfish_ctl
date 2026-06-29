"""Dual-mode tests for the compute settings command."""
import json

from idrac_ctl.idrac_manager import IDracManager
from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_compute_query_returns_system_settings_for_610_plus_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """compute-query returns the ComputerSystem Settings resource on iDRAC 6.10+."""
    monkeypatch.setattr(
        IDracManager,
        "idrac_manager_version",
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
