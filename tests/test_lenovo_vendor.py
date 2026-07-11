"""Offline proof that core discovery handles Lenovo XCC Redfish shapes.

The fixture slice is seeded from Lenovo XCC public REST API examples. XCC uses
``Systems/1`` and ``Managers/1`` ids, exposes BIOS pending settings at
``Systems/1/Bios/Pending``, and documents category-prefixed BIOS attributes such
as ``Memory_MemorySpeed``.

Author Mus spyroot@gmail.com
"""


def test_lenovo_discovery_resolves_xcc_ids(redfish_mock_factory):
    """Discovery finds Lenovo's documented ids instead of Dell/Supermicro ids."""
    mgr, _ = redfish_mock_factory("lenovo")
    assert mgr.redfish_vendor == "Lenovo"
    assert mgr.discover_computer_system_ids() == ["/redfish/v1/Systems/1"]
    assert mgr.discover_manager_ids() == ["/redfish/v1/Managers/1"]
    assert mgr.idrac_manage_servers == "/redfish/v1/Systems/1"


def test_lenovo_bios_pending_uses_category_prefixed_attributes(redfish_mock_factory):
    """Lenovo pending BIOS attributes retain their category-prefixed names."""
    mgr, _ = redfish_mock_factory("lenovo")
    pending = mgr.base_query("/redfish/v1/Systems/1/Bios/Pending").data
    assert pending["AttributeRegistry"] == "BiosAttributeRegistry.1.0.0"
    assert pending["Attributes"]["Memory_MemorySpeed"] == "MaxPerformance"
    assert pending["Attributes"]["Processors_CStates"] == "Disable"


def test_lenovo_updateservice_exposes_simple_and_push_update_paths(redfish_mock_factory):
    """The XCC UpdateService fixture pins SimpleUpdate plus push URI fallbacks."""
    mgr, _ = redfish_mock_factory("lenovo")
    update_service = mgr.base_query("/redfish/v1/UpdateService").data
    assert update_service["Actions"]["#UpdateService.SimpleUpdate"]["target"] == (
        "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"
    )
    assert update_service["HttpPushUri"] == "/fwupdate"
    assert update_service["MultipartHttpPushUri"] == "/mfwupdate"
