"""Offline tests for the vendor capability registry.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.vendors import all_vendors, get_vendor
from redfish_ctl.vendors.base import VendorCapabilities


def test_dell_profile_registered_with_doc_facts():
    """The Dell profile reflects the documented iDRAC capabilities."""
    dell = get_vendor("dell")
    assert dell.vendor == "dell"
    assert dell.oem_prefix == "Dell"
    # all five query params, but only one per URI
    assert dell.query_select and dell.query_filter and dell.query_expand
    assert dell.query_top and dell.query_only
    assert dell.one_query_param_per_uri is True
    # recurring jobs, one per type, with the documented schedulable URIs
    assert dell.job_scheduling and dell.one_recurring_job_per_type
    assert any("ComputerSystem.Reset" in u for u in dell.schedulable_uris)
    assert any("LogService.ClearLog" in u for u in dell.schedulable_uris)
    assert dell.lifecycle_events_sse is True


def test_unknown_vendor_falls_back_to_generic():
    """An unknown vendor yields the conservative generic profile."""
    caps = get_vendor("acme")
    assert caps.vendor == "generic"
    # generic is conservative: no select/filter/scheduling by default
    assert caps.query_select is False
    assert caps.job_scheduling is False


def test_none_vendor_is_generic():
    """None resolves to the generic profile, not an error."""
    assert get_vendor(None).vendor == "generic"


def test_registry_contains_scaffolded_vendors():
    """Dell, HPE, Supermicro and generic are all registered."""
    names = set(all_vendors())
    assert {"dell", "hpe", "supermicro", "generic"} <= names


def test_capabilities_are_immutable():
    """Profiles are frozen so a command cannot mutate shared state."""
    dell = get_vendor("dell")
    try:
        dell.job_scheduling = False  # type: ignore[misc]
    except Exception as exc:
        assert isinstance(exc, (AttributeError, TypeError))
    else:
        raise AssertionError("VendorCapabilities should be immutable")
    assert isinstance(dell, VendorCapabilities)
