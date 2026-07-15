"""Dual-mode test for the console-info command."""
import json

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_console_info_dualmode_returns_read_only_console_rows(request, monkeypatch):
    """console-info reports Manager console capabilities without mutating state."""
    monkeypatch.delenv("IDRAC_IP", raising=False)
    redfish_api = request.getfixturevalue("redfish_api")
    redfish_service = request.getfixturevalue("redfish_service")

    result = redfish_api.sync_invoke(ApiRequestType.ConsoleInfo, "console-info")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    json.dumps(result.data)
    assert result.data == [
        {
            "Manager": "iDRAC.Embedded.1",
            "Console": "SerialConsole",
            "Enabled": True,
            "ConnectTypes": ["IPMI", "SSH"],
            "MaxSessions": 4,
        },
        {
            "Manager": "iDRAC.Embedded.1",
            "Console": "GraphicalConsole",
            "Enabled": True,
            "ConnectTypes": ["KVMIP"],
            "MaxSessions": 2,
        },
        {
            "Manager": "iDRAC.Embedded.1",
            "Console": "CommandShell",
            "Enabled": True,
            "ConnectTypes": ["SSH"],
            "MaxSessions": 4,
        },
    ]
    assert redfish_service.requests
    assert all(
        recorded.method not in {"POST", "PATCH", "DELETE"}
        for recorded in redfish_service.requests
    )
