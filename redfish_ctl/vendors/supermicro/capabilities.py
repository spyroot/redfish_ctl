"""Supermicro capability profile.

Validated read-only against a live Supermicro GB300 BMC (Redfish 1.17.0):
Manufacturer reports "Supermicro"; ServiceRoot carries no Oem block; standard
Redfish paths are used (Systems member id "System_0", not Dell's
"System.Embedded.1"); UpdateService and its FirmwareInventory exist.

Server-side query-parameter support and recurring JobService scheduling were NOT
observed, so they stay conservative (False) until verified against hardware.

Author Mus spyroot@gmail.com
"""
from ..base import VendorCapabilities, register

SUPERMICRO = register(
    VendorCapabilities(
        vendor="supermicro",
        # Manufacturer string reported by the live GB300 BMC.
        oem_prefix="Supermicro",
        # Query parameters and job scheduling were not validated on the GB300;
        # keep them conservative (False) until confirmed against hardware.
        query_select=False,
        query_filter=False,
        query_expand=False,
        query_top=False,
        query_only=False,
        job_scheduling=False,
        one_recurring_job_per_type=False,
    )
)
