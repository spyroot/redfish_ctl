"""HPE iLO capability profile (placeholder).

Scaffolding for a future HPE iLO vendor package. Values are conservative until
validated against real iLO / the HPE iLO Redfish emulator. Fill in as iLO command
modules are added to this package.

Author Mus spyroot@gmail.com
"""
from ..base import VendorCapabilities, register

HPE_SUPPORTED_RESOURCES = (
    "/redfish/v1",
    "/redfish/v1/Systems",
    "/redfish/v1/Chassis",
    "/redfish/v1/Managers",
    "/redfish/v1/UpdateService",
    "/redfish/v1/EventService",
    "/redfish/v1/TelemetryService",
)

HPE_SUPPORTED_ACTIONS = (
    "#ComputerSystem.Reset",
    "#Manager.Reset",
    "#UpdateService.SimpleUpdate",
    "#LogService.ClearLog",
    "#EventService.SubmitTestEvent",
)

HPE = register(
    VendorCapabilities(
        vendor="hpe",
        oem_prefix="Hpe",
        # Unverified — keep generic defaults until tested against iLO.
        query_expand=True,
        supported_resources=HPE_SUPPORTED_RESOURCES,
        supported_actions=HPE_SUPPORTED_ACTIONS,
    )
)
