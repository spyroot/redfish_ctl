"""Offline tests for multi-member Systems/Managers discovery.

A Dell box exposes one System (System.Embedded.1) and one Manager
(iDRAC.Embedded.1), but a Supermicro GB300 exposes two of each
(System_0 + HGX_Baseboard_0, BMC_0 + HGX_BMC_0). These pin
``discover_computer_system_ids`` / ``discover_manager_ids`` / ``_member_ids``
returning ALL members so callers can stop assuming a singleton. No network.
"""
import pytest

from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager import CommandResult


class TestMemberIds:
    """RedfishManagerBase._member_ids extracts @odata.id from a Members list."""

    def test_member_ids_extracts_all(self):
        """A two-member list yields both ids, in order."""
        members = [
            {"@odata.id": "/redfish/v1/Systems/System_0"},
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
        ]
        assert RedfishManagerBase._member_ids(members) == [
            "/redfish/v1/Systems/System_0",
            "/redfish/v1/Systems/HGX_Baseboard_0",
        ]

    def test_member_ids_skips_missing_key(self):
        """A member without @odata.id is skipped, not fatal."""
        members = [
            {"@odata.id": "/redfish/v1/Systems/System_0"},
            {"name": "no_id"},
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
        ]
        assert RedfishManagerBase._member_ids(members) == [
            "/redfish/v1/Systems/System_0",
            "/redfish/v1/Systems/HGX_Baseboard_0",
        ]

    def test_member_ids_skips_non_string_id(self):
        """Non-string @odata.id values are skipped."""
        members = [
            {"@odata.id": "/redfish/v1/Systems/System_0"},
            {"@odata.id": 123},
            {"@odata.id": None},
        ]
        assert RedfishManagerBase._member_ids(members) == ["/redfish/v1/Systems/System_0"]

    @pytest.mark.parametrize("bad", [None, "x", {}, 5])
    def test_member_ids_non_list_input(self, bad):
        """A non-list payload returns [] rather than raising."""
        assert RedfishManagerBase._member_ids(bad) == []


def _manager_with(members):
    """Build an RedfishManagerBase (no __init__) whose base_query serves ``members``."""
    inst = RedfishManagerBase.__new__(RedfishManagerBase)
    inst.base_query = lambda *a, **k: CommandResult(members, None, None, None)
    return inst


def test_discover_computer_system_ids_returns_all():
    """discover_computer_system_ids enumerates the Systems collection."""
    inst = _manager_with([
        {"@odata.id": "/redfish/v1/Systems/System_0"},
        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
    ])
    assert inst.discover_computer_system_ids() == [
        "/redfish/v1/Systems/System_0",
        "/redfish/v1/Systems/HGX_Baseboard_0",
    ]


def test_discover_manager_ids_returns_all():
    """discover_manager_ids enumerates the Managers collection (BMC_0, HGX_BMC_0)."""
    inst = _manager_with([
        {"@odata.id": "/redfish/v1/Managers/BMC_0"},
        {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
    ])
    assert inst.discover_manager_ids() == [
        "/redfish/v1/Managers/BMC_0",
        "/redfish/v1/Managers/HGX_BMC_0",
    ]
