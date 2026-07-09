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
from .base import VendorCapabilities, all_vendors
from .base import get as get_vendor
from .dell import capabilities as _dell  # noqa: F401
from .hpe import capabilities as _hpe  # noqa: F401
from .supermicro import capabilities as _supermicro  # noqa: F401

__all__ = ["VendorCapabilities", "get_vendor", "all_vendors"]
