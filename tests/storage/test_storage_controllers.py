"""Dual-mode tests for the storage-controllers query command (storage_query).

Runs offline by default against the mock service and against real hardware when
IDRAC_IP is set. storage_query is a pure read: it GETs the ``{system}/Storage``
collection and returns the controller ids plus their Redfish URIs, so the tests
also assert the command never issues a mutating request and stays vendor-neutral
(no Dell ``JID_`` literal leaks into a read result).

Author Mus spyroot@gmail.com
"""
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_CONTROLLER = "RAID.Integrated.1-1"


def test_storage_controllers_lists_ids_and_uris_read_only(redfish_mock, redfish_service):
    """storage_query returns controller ids and matching URIs using only GETs.

    The Dell Storage fixture advertises a single ``RAID.Integrated.1-1`` member;
    the command must surface it as an id (data) and a URI (discovered) without any
    PATCH/POST/DELETE.
    """
    result = redfish_mock.sync_invoke(
        ApiRequestType.StorageQuery, "storage_query", id_filter=""
    )

    assert isinstance(result, CommandResult)
    assert _CONTROLLER in result.data
    assert any(str(uri).endswith(f"/{_CONTROLLER}") for uri in result.discovered)
    assert {request.method for request in redfish_service.requests} == {"GET"}


def test_storage_controllers_applies_id_filter(redfish_mock):
    """storage_query --filter keeps only controllers whose URI contains the token."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.StorageQuery, "storage_query", id_filter="RAID"
    )

    assert result.data == [_CONTROLLER]
    assert result.discovered == [
        f"/redfish/v1/Systems/System.Embedded.1/Storage/{_CONTROLLER}"
    ]


def test_storage_controllers_filter_excludes_non_matching_ids(redfish_mock_factory):
    """A filter token that matches no controller URI yields an empty result.

    Seeds a generic (non-Dell) Storage collection so the edge case is exercised on
    a vendor-neutral tree; the filter ``ZZZ`` matches neither member.
    """
    mgr, service = redfish_mock_factory("generic")
    storage_path = f"{mgr.idrac_manage_servers}/Storage"
    service._overlay[storage_path.lower()] = {
        "@odata.id": storage_path,
        "Members": [
            {"@odata.id": f"{storage_path}/NVMe.Slot.1"},
            {"@odata.id": f"{storage_path}/SATA.Slot.2"},
        ],
    }

    result = mgr.sync_invoke(ApiRequestType.StorageQuery, "storage_query", id_filter="ZZZ")

    assert result.data == []
    assert result.discovered == []


def test_storage_controllers_vendor_neutral_generic(redfish_mock_factory):
    """storage_query lists controllers on a generic tree with no Dell literal.

    A read must behave identically regardless of vendor; the generic path returns
    the seeded controller ids and no ``JID_`` (a Dell job literal) can appear in a
    read result.
    """
    mgr, service = redfish_mock_factory("generic")
    storage_path = f"{mgr.idrac_manage_servers}/Storage"
    service._overlay[storage_path.lower()] = {
        "@odata.id": storage_path,
        "Members": [
            {"@odata.id": f"{storage_path}/NVMe.Slot.1"},
            {"@odata.id": f"{storage_path}/SATA.Slot.2"},
        ],
    }

    result = mgr.sync_invoke(ApiRequestType.StorageQuery, "storage_query", id_filter="")

    assert isinstance(result, CommandResult)
    assert result.data == ["NVMe.Slot.1", "SATA.Slot.2"]
    assert all("JID_" not in str(uri) for uri in result.discovered)
