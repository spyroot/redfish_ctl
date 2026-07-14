"""Corpus test for the captured Supermicro X10SDV-TLN4F fixtures.

A real read-only discovery capture from a home-lab X10SDV (BMC 4.00, Redfish
1.0.1) committed as a test corpus — the "thin firmware" counterpart to the fuller
GB300 supermicro_fixtures. Served via redfish_mock_factory("supermicro_x10").
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_x10_serviceroot_is_redfish_101(redfish_mock_factory):
    """The X10 ServiceRoot reports the frozen Redfish 1.0.1 (not the DMTF base)."""
    mgr, _ = redfish_mock_factory("supermicro_x10")
    root = mgr.base_query("/redfish/v1/").data
    assert root.get("RedfishVersion") == "1.0.1"


def test_x10_system_is_supermicro(redfish_mock_factory):
    """The captured System resolves to a Supermicro host."""
    mgr, _ = redfish_mock_factory("supermicro_x10")
    systems = mgr.base_query("/redfish/v1/Systems").data
    member = systems["Members"][0]["@odata.id"]
    sysd = mgr.base_query(member).data
    assert sysd.get("Manufacturer") == "Supermicro"


def test_x10_console_info_reads_from_corpus(redfish_mock_factory):
    """A vendor-neutral read command (console-info) drives the X10 corpus."""
    mgr, _ = redfish_mock_factory("supermicro_x10")
    rows = mgr.sync_invoke(ApiRequestType.ConsoleInfo, "console-info").data
    assert isinstance(rows, list)
