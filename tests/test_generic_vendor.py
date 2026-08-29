"""Offline proof that the commands work on a product-neutral Redfish tree.

Uses DMTF's public-rackmount1 mockup (tests/generic_fixtures/) — a fourth,
vendor-agnostic shape with id `Systems/437XR1138R2` — as an independent check
that discovery and the link-navigated commands make no vendor assumptions.
"""
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import RedfishManager


def _generic_manager(redfish_mock_factory):
    """Mount the generic fixture tree and return the neutral DMTF manager."""
    _, service = redfish_mock_factory("generic")
    manager = RedfishManager(
        host="mock-generic",
        username="root",
        password="mock",
        insecure=True,
        is_debug=False,
    )
    return manager, service


def test_generic_discovery(redfish_mock_factory):
    """Discovery resolves the generic system id (no Dell/SMC/HPE assumption)."""
    mgr, _ = _generic_manager(redfish_mock_factory)
    systems = mgr.discover_computer_system_ids()
    assert systems == ["/redfish/v1/Systems/437XR1138R2"]


def test_generic_read_commands(redfish_mock_factory):
    """Core read commands return data on a standard Redfish tree."""
    mgr, _ = _generic_manager(redfish_mock_factory)
    assert mgr.sync_invoke(ApiRequestType.Sensors, "sensors").data
    assert mgr.sync_invoke(ApiRequestType.EthernetInterfaces, "ethernet-interfaces").data
    assert mgr.sync_invoke(ApiRequestType.ComponentIntegrity, "component-integrity").data
    # action discovery + guarded reset resolve on the generic tree too
    listed = mgr.sync_invoke(ApiRequestType.ActionList, "action_list")
    assert any(r["FullType"] == "#ComputerSystem.Reset" for r in listed.data)
