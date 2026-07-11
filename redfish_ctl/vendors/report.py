"""Machine-readable vendor capability reports."""

from typing import Optional

from .base import all_vendors, get

REPORT_SCHEMA = "redfish_ctl.capability_report.v1"


def capability_report(vendor: Optional[str] = None) -> dict:
    """Return registered vendor capability profiles for IaC consumers."""
    if vendor:
        caps = get(vendor)
        vendors = {caps.vendor: caps.to_dict()}
    else:
        vendors = {
            name: caps.to_dict()
            for name, caps in sorted(all_vendors().items())
        }

    return {
        "schema": REPORT_SCHEMA,
        "summary": {"vendor_count": len(vendors)},
        "vendors": vendors,
    }
