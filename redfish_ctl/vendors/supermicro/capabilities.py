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

SUPERMICRO_SUPPORTED_RESOURCES = (
    "/redfish/v1",
    "/redfish/v1/Systems",
    "/redfish/v1/Chassis",
    "/redfish/v1/Managers",
    "/redfish/v1/UpdateService",
    "/redfish/v1/EventService",
    "/redfish/v1/TelemetryService",
    "/redfish/v1/AccountService",
)

SUPERMICRO_SUPPORTED_ACTIONS = (
    "#ComputerSystem.Reset",
    "#Chassis.Reset",
    "#Manager.Reset",
    "#NetworkAdapter.Reset",
    "#NvidiaWorkloadPower.EnableProfiles",
    "#Control.ResetToDefaults",
)

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
        supported_resources=SUPERMICRO_SUPPORTED_RESOURCES,
        supported_actions=SUPERMICRO_SUPPORTED_ACTIONS,
    )
)
