"""Offline test for the vendor-neutral console-info command.

Reports the Manager's SerialConsole/GraphicalConsole/CommandShell capability
(not a live stream). Verified on the HPE iLO and GB300 corpora.
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_console_info_ilo(redfish_mock_factory):
    """console-info reports iLO serial + graphical + shell console access."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.ConsoleInfo, "console-info")
    assert isinstance(result.data, list) and result.data
    kinds = {r["Console"] for r in result.data}
    assert "SerialConsole" in kinds and "GraphicalConsole" in kinds
    serial = next(r for r in result.data if r["Console"] == "SerialConsole")
    # the reported connect types are how you'd reach the live console (SOL/SSH)
    assert serial["ConnectTypes"]


def test_console_info_supermicro(redfish_mock_factory):
    """console-info also resolves on the GB300 tree (vendor-neutral)."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.ConsoleInfo, "console-info")
    assert isinstance(result.data, list) and result.data
    assert any(r["Console"] == "SerialConsole" for r in result.data)
