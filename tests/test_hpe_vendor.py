"""Offline proof that redfish_ctl is vendor-neutral on an HPE iLO tree.

Serves a redacted-free slice of an HPE ProLiant DL380a (iLO) Redfish tree
(tests/hpe_fixtures/, from HPE's BSD-3 iLO emulator mockups) through the real
requests path via redfish_mock_factory("hpe"). HPE uses a THIRD id scheme
(Systems/1, Managers/1) — neither Dell's System.Embedded.1 nor Supermicro's
System_0 — so these tests prove the discovery-first, link-navigated commands
work on a vendor they were never special-cased for.
"""
from redfish_ctl.command_shared import ApiRequestType


def test_hpe_discovery_resolves_ilo_ids(redfish_mock_factory):
    """Discovery finds the real iLO ids, not a Dell/Supermicro id."""
    mgr, _ = redfish_mock_factory("hpe")
    assert mgr.discover_computer_system_ids() == ["/redfish/v1/Systems/1"]
    assert mgr.discover_manager_ids() == ["/redfish/v1/Managers/1"]
    # the host system resolver lands on the iLO system
    assert mgr.idrac_manage_servers == "/redfish/v1/Systems/1"
    assert "/redfish/v1/Systems/System.Embedded.1" not in mgr.discover_computer_system_ids()


def test_hpe_read_commands_return_data(redfish_mock_factory):
    """The generic read commands surface data on iLO (modern Sensors/Telemetry)."""
    mgr, _ = redfish_mock_factory("hpe")
    assert mgr.sync_invoke(ApiRequestType.Sensors, "sensors").data, "no iLO sensors"
    assert mgr.sync_invoke(ApiRequestType.NetworkAdapters, "network-adapters").data
    assert mgr.sync_invoke(ApiRequestType.ComponentIntegrity, "component-integrity").data
    assert mgr.sync_invoke(ApiRequestType.MetricReports, "metric-reports").data
    assert mgr.sync_invoke(ApiRequestType.MetricReportDefinitions, "metric-definitions").data


def test_hpe_nvlink_ports_empty_not_error(redfish_mock_factory):
    """A box with no NVIDIA NVLink model yields [] — tolerant, not a crash."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.NvLinkPorts, "nvlink-ports")
    assert result.data == []


def test_hpe_action_discovery_and_guard(redfish_mock_factory):
    """actions lists iLO action targets; system-reset resolves + guards on iLO."""
    mgr, svc = redfish_mock_factory("hpe")
    listed = mgr.sync_invoke(ApiRequestType.ActionList, "action_list")
    fulls = {r["FullType"] for r in listed.data}
    assert "#ComputerSystem.Reset" in fulls

    # destructive reset resolves the iLO target and stays a dry-run without --confirm
    reset = mgr.sync_invoke(ApiRequestType.SystemReset, "system_reset",
                            reset_type="GracefulRestart")
    assert reset.data["dry_run"] is True
    assert reset.data["target"] == "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
    assert reset.data["level"] == "destructive"
    assert sum(1 for r in svc.requests if r.method == "POST") == 0
