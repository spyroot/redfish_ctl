"""HPE iLO capability profile (placeholder).

Scaffolding for a future HPE iLO vendor package. Values are conservative until
validated against real iLO / the HPE iLO Redfish emulator. Fill in as iLO command
modules are added to this package.

Author Mus spyroot@gmail.com
"""
from ..base import VendorCapabilities, register

HPE = register(
    VendorCapabilities(
        vendor="hpe",
        oem_prefix="Hpe",
        # Unverified — keep generic defaults until tested against iLO.
        query_expand=True,
    )
)
