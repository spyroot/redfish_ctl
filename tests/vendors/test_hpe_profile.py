"""Offline tests for the HPE iLO vendor capability profile.

The profile is intentionally conservative while iLO-specific command behavior
is built out. These tests pin the one known query capability and the disabled
defaults so unverified features are not silently enabled.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.vendors import get_vendor
from redfish_ctl.vendors.base import VendorCapabilities


def test_hpe_oem_prefix_matches_ilo_oem_namespace():
    """HPE iLO uses the Hpe OEM namespace; the profile mirrors that prefix."""
    caps = get_vendor("hpe")
    assert caps.vendor == "hpe"
    assert caps.oem_prefix == "Hpe"


def test_hpe_query_params_keep_only_verified_expand_enabled():
    """Only server-side expand is pinned; other query parameters stay disabled."""
    caps = get_vendor("hpe")
    assert caps.query_expand is True
    assert caps.query_select is False
    assert caps.query_filter is False
    assert caps.query_top is False
    assert caps.query_only is False
    assert caps.one_query_param_per_uri is False


def test_hpe_job_scheduling_is_conservative():
    """Recurring JobService scheduling is unverified, so it stays disabled."""
    caps = get_vendor("hpe")
    assert caps.job_scheduling is False
    assert caps.one_recurring_job_per_type is False
    assert caps.schedulable_uris == ()


def test_hpe_profile_is_frozen():
    """The profile is immutable so a command cannot mutate shared state."""
    caps = get_vendor("hpe")
    assert isinstance(caps, VendorCapabilities)
    try:
        caps.oem_prefix = "Dell"  # type: ignore[misc]
    except Exception as exc:
        assert isinstance(exc, (AttributeError, TypeError))
    else:
        raise AssertionError("VendorCapabilities should be immutable")
