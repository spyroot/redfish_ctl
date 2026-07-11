"""Vendor capability model.

Each vendor's Redfish implementation differs (which query parameters it honors
server-side, whether it supports recurring jobs, which OEM paths exist). A
``VendorCapabilities`` profile declares those facts so vendor-specific commands
and behaviors can be gated cleanly — only run where the target actually supports
them. The generic core (``RedfishManager``) stays product-neutral; vendor
packages register a profile here.

Author Mus spyroot@gmail.com
"""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class VendorCapabilities:
    """What a vendor's Redfish service supports. Conservative defaults (generic)."""

    vendor: str
    oem_prefix: Optional[str] = None          # e.g. "Dell"

    # Server-side Redfish query parameters honored by this vendor.
    query_select: bool = False
    query_filter: bool = False
    query_expand: bool = True
    query_top: bool = False
    query_only: bool = False
    # Some vendors (Dell) accept only one query parameter per URI.
    one_query_param_per_uri: bool = False

    # JobService recurring/scheduled jobs.
    job_scheduling: bool = False
    one_recurring_job_per_type: bool = False
    schedulable_uris: Tuple[str, ...] = field(default_factory=tuple)

    # Redfish Lifecycle Events over Server-Sent Events.
    lifecycle_events_sse: bool = False

    # Capability-field name -> short local fixture/source provenance note.
    evidence: Mapping[str, str] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_dict(self) -> Dict[str, object]:
        """Return a JSON-ready representation of the capability profile."""
        return {
            "vendor": self.vendor,
            "oem_prefix": self.oem_prefix,
            "query_select": self.query_select,
            "query_filter": self.query_filter,
            "query_expand": self.query_expand,
            "query_top": self.query_top,
            "query_only": self.query_only,
            "one_query_param_per_uri": self.one_query_param_per_uri,
            "job_scheduling": self.job_scheduling,
            "one_recurring_job_per_type": self.one_recurring_job_per_type,
            "schedulable_uris": list(self.schedulable_uris),
            "lifecycle_events_sse": self.lifecycle_events_sse,
            "evidence": dict(self.evidence),
        }


_REGISTRY: Dict[str, VendorCapabilities] = {}


def register(caps: VendorCapabilities) -> VendorCapabilities:
    """Register a vendor capability profile (idempotent)."""
    _REGISTRY[caps.vendor] = caps
    return caps


def get(vendor: Optional[str]) -> VendorCapabilities:
    """Return the profile for ``vendor``, or the generic profile if unknown."""
    if vendor is None:
        return GENERIC
    return _REGISTRY.get(vendor.lower(), GENERIC)


def all_vendors() -> Dict[str, VendorCapabilities]:
    """A copy of the registry."""
    return dict(_REGISTRY)


# Conservative baseline used when the target vendor is unknown.
GENERIC = register(VendorCapabilities(vendor="generic"))
