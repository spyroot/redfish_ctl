"""Offline proof that the commands work on a product-neutral Redfish tree.

Uses DMTF's public-rackmount1 mockup (tests/generic_fixtures/) — a fourth,
vendor-agnostic shape with id `Systems/437XR1138R2` — as an independent check
that discovery and the link-navigated commands make no vendor assumptions.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def test_generic_discovery(redfish_mock_factory):
    """Discovery resolves the generic system id (no Dell/SMC/HPE assumption)."""
    mgr, _ = redfish_mock_factory("generic")
    systems = mgr.discover_computer_system_ids()
    assert systems == ["/redfish/v1/Systems/437XR1138R2"]


def test_generic_read_commands(redfish_mock_factory):
    """Core read commands return data on a standard Redfish tree."""
    mgr, _ = redfish_mock_factory("generic")
    assert mgr.sync_invoke(ApiRequestType.Sensors, "sensors").data
    assert mgr.sync_invoke(ApiRequestType.EthernetInterfaces, "ethernet-interfaces").data
    assert mgr.sync_invoke(ApiRequestType.ComponentIntegrity, "component-integrity").data
    # action discovery + guarded reset resolve on the generic tree too
    listed = mgr.sync_invoke(ApiRequestType.ActionList, "action_list")
    assert any(r["FullType"] == "#ComputerSystem.Reset" for r in listed.data)
