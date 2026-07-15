"""Offline test: storage-list is vendor-neutral and degrades gracefully.

storage-list uses the standard ``{system}/Storage`` subpath. It previously raised
a 404 on hosts that expose Storage under a different shape; it now returns the
collection where present and an empty result (no crash) where absent.
"""
from redfish_ctl.redfish_manager_shared import ApiRequestType


def test_storage_list_on_ilo(redfish_mock_factory):
    """storage-list returns the iLO Storage collection."""
    mgr, _ = redfish_mock_factory("hpe")
    result = mgr.sync_invoke(ApiRequestType.StorageListQuery, "storage_list")
    assert isinstance(result.data, dict)
    assert result.data.get("Members"), "no iLO storage controllers"


def test_storage_list_on_supermicro(redfish_mock_factory):
    """storage-list resolves on the GB300 tree too (vendor-neutral)."""
    mgr, _ = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.StorageListQuery, "storage_list")
    assert isinstance(result.data, dict)
