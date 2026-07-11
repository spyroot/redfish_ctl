"""Vendor-specific Redfish capabilities and (future) command modules.

Each vendor gets its own subdirectory under ``vendors/`` so vendor specifics are
clearly separated from the product-neutral core. Importing this package registers
every vendor's capability profile. See README.md for the convention.

    from redfish_ctl.vendors import get_vendor
    caps = get_vendor("dell")
    if caps.job_scheduling:
        ...

Author Mus spyroot@gmail.com
"""
# Importing each vendor's capabilities module registers its profile.
from typing import Any, Mapping, Optional

from redfish_ctl.discover.classifier import classify_vendor

from .base import VendorCapabilities, all_vendors
from .base import get as get_vendor
from .dell import capabilities as _dell  # noqa: F401
from .hpe import capabilities as _hpe  # noqa: F401
from .lenovo import capabilities as _lenovo  # noqa: F401
from .report import capability_report
from .supermicro import capabilities as _supermicro  # noqa: F401


def capabilities_for_service_root(
        service_root: Optional[Mapping[str, Any]]) -> VendorCapabilities:
    """Return the capability profile for a parsed Redfish ServiceRoot."""
    return get_vendor(classify_vendor(service_root))


__all__ = [
    "VendorCapabilities",
    "get_vendor",
    "all_vendors",
    "capabilities_for_service_root",
    "capability_report",
]
