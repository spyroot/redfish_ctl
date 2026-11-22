"""Offline tests for the Supermicro vendor capability profile.

The profile was filled from read-only observations of a live Supermicro GB300
BMC (Redfish 1.17.0). These tests pin the OEM prefix and the conservative
defaults so an unverified capability is never silently flipped on.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.vendors import get_vendor
from redfish_ctl.vendors.base import VendorCapabilities


def test_supermicro_oem_prefix_matches_manufacturer():
    """The GB300 BMC reports Manufacturer "Supermicro"; oem_prefix mirrors it."""
    caps = get_vendor("supermicro")
    assert caps.vendor == "supermicro"
    assert caps.oem_prefix == "Supermicro"


def test_supermicro_query_params_are_conservative():
    """Server-side query parameters were not verified, so all stay False."""
    caps = get_vendor("supermicro")
    assert caps.query_select is False
    assert caps.query_filter is False
    assert caps.query_expand is False
    assert caps.query_top is False
    assert caps.query_only is False
    assert caps.one_query_param_per_uri is False


def test_supermicro_job_scheduling_is_conservative():
    """Recurring JobService scheduling was unverified, so it stays disabled."""
    caps = get_vendor("supermicro")
    assert caps.job_scheduling is False
    assert caps.one_recurring_job_per_type is False
    assert caps.schedulable_uris == ()


def test_supermicro_profile_is_frozen():
    """The profile is immutable so a command cannot mutate shared state."""
    caps = get_vendor("supermicro")
    assert isinstance(caps, VendorCapabilities)
    try:
        caps.oem_prefix = "Dell"  # type: ignore[misc]
    except Exception as exc:
        assert isinstance(exc, (AttributeError, TypeError))
    else:
        raise AssertionError("VendorCapabilities should be immutable")
