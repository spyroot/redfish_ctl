"""Offline tests for the Lenovo XCC vendor capability profile.

Lenovo XCC documents query support in ServiceRoot.ProtocolFeaturesSupported,
but job scheduling and event streaming are not pinned by the docs fixture slice.
These tests keep the profile explicit and conservative where behavior is not
verified.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.vendors import all_vendors, get_vendor
from redfish_ctl.vendors.base import VendorCapabilities


def test_lenovo_profile_registered_with_xcc_oem_prefix():
    """Lenovo XCC uses the Lenovo OEM namespace; the profile mirrors that prefix."""
    caps = get_vendor("lenovo")
    assert caps.vendor == "lenovo"
    assert caps.oem_prefix == "Lenovo"
    assert "lenovo" in all_vendors()


def test_lenovo_query_capabilities_match_xcc_service_root():
    """XCC advertises select/filter/expand/only support; top stays unverified."""
    caps = get_vendor("lenovo")
    assert caps.query_select is True
    assert caps.query_filter is True
    assert caps.query_expand is True
    assert caps.query_only is True
    assert caps.query_top is False
    assert caps.one_query_param_per_uri is False


def test_lenovo_job_scheduling_is_conservative():
    """Recurring JobService scheduling is unverified, so it stays disabled."""
    caps = get_vendor("lenovo")
    assert caps.job_scheduling is False
    assert caps.one_recurring_job_per_type is False
    assert caps.schedulable_uris == ()
    assert caps.lifecycle_events_sse is False


def test_lenovo_profile_is_frozen():
    """The profile is immutable so a command cannot mutate shared state."""
    caps = get_vendor("lenovo")
    assert isinstance(caps, VendorCapabilities)
    try:
        caps.oem_prefix = "Dell"  # type: ignore[misc]
    except Exception as exc:
        assert isinstance(exc, (AttributeError, TypeError))
    else:
        raise AssertionError("VendorCapabilities should be immutable")
