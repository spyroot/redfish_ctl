"""Dual-mode tests for the vendor-neutral oem-info command."""
import json

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_oem_info_dualmode_reports_dell_oem_rows_without_mutation(
    request,
    monkeypatch,
):
    """oem-info reports Dell OEM extensions through the mock transport."""
    monkeypatch.delenv("REDFISH_IP", raising=False)
    monkeypatch.delenv("REDFISH_USERNAME", raising=False)
    monkeypatch.delenv("REDFISH_PASSWORD", raising=False)
    redfish_api = request.getfixturevalue("redfish_api")
    redfish_service = request.getfixturevalue("redfish_service")

    result = redfish_api.sync_invoke(ApiRequestType.OemInfo, "oem-info")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data, sort_keys=True)
    assert result.data == [
        {
            "Resource": "iDRAC.Embedded.1",
            "Vendor": "Dell",
            "Type": "#DellManager.v1_0_0.DellManager",
            "Keys": ["LifecycleControllerVersion"],
        }
    ]
    assert redfish_service.requests
    assert all(
        request.method not in {"POST", "PATCH", "DELETE"}
        for request in redfish_service.requests
    )
