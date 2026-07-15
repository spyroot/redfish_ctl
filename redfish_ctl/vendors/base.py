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

    # Fixture-backed conformance claims. Keep these empty until a committed
    # vendor tree proves the resource root or action exists for that vendor.
    supported_resources: Tuple[str, ...] = field(default_factory=tuple)
    supported_actions: Tuple[str, ...] = field(default_factory=tuple)

    # Capability-field name -> short local fixture/source provenance note.
    evidence: Mapping[str, str] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        """Freeze the ``evidence`` mapping into a read-only proxy."""
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_dict(self) -> Dict[str, object]:
        """Return a JSON-ready representation of the capability profile.

        :return: a dict mapping capability field names to their values, safe to
            serialize as JSON.
        """
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
    """Register a vendor capability profile (idempotent).

    :param caps: the capability profile to store, keyed by ``caps.vendor``.
    :return: the same profile that was registered.
    """
    _REGISTRY[caps.vendor] = caps
    return caps


def get(vendor: Optional[str]) -> VendorCapabilities:
    """Return the profile for ``vendor``, or the generic profile if unknown.

    :param vendor: vendor name to look up (case-insensitive); ``None`` yields the
        generic profile.
    :return: the registered :class:`VendorCapabilities` profile, or ``GENERIC``
        when the vendor is ``None`` or not registered.
    """
    if vendor is None:
        return GENERIC
    return _REGISTRY.get(vendor.lower(), GENERIC)


def all_vendors() -> Dict[str, VendorCapabilities]:
    """Return a copy of the registry.

    :return: a new dict mapping vendor name to its :class:`VendorCapabilities`
        profile.
    """
    return dict(_REGISTRY)


# Conservative baseline used when the target vendor is unknown.
GENERIC = register(VendorCapabilities(vendor="generic"))
