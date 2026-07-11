"""Dell iDRAC capability profile.

Encodes facts from the Dell iDRAC Redfish API docs (query parameters, recurring
JobService scheduling, lifecycle events). Dell-specific command modules migrate
into this package incrementally (today they live in ``redfish_ctl/idrac_manager.py``
and ``redfish_ctl/delloem/``); this profile lets commands gate Dell-only behavior.

Author Mus spyroot@gmail.com
"""
from ..base import VendorCapabilities, register

# Redfish URIs for which Dell iDRAC supports recurring/scheduled jobs.
# {placeholders} are filled per-target at runtime.
DELL_SCHEDULABLE_URIS = (
    "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
    "/redfish/v1/Chassis/System.Embedded.1/Actions/Chassis.Reset",
    "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Manager.Reset",
    "/redfish/v1/Systems/System.Embedded.1/Storage/Volumes/{volume_id}/Actions/Volume.CheckConsistency",
    "/redfish/v1/Managers/{manager_id}/LogServices/Sel/Actions/LogService.ClearLog",
    "/redfish/v1/Systems/System.Embedded.1/Bios/Actions/Oem/DellBios.RunBIOSLiveScanning",
)

DELL_SERVICE_ROOT_EVIDENCE = (
    "tests/idrac_fixtures/_redfish_v1.json: "
    "Dell ServiceRoot fixture for query-gated reads"
)
DELL_JOB_SERVICE_EVIDENCE = (
    "tests/idrac_fixtures/_redfish_v1_Managers_iDRAC.Embedded.1_"
    "Oem_Dell_DellJobService.json: Dell OEM job service fixture"
)
DELL_EVENT_SERVICE_EVIDENCE = (
    "tests/idrac_fixtures/_redfish_v1_EventService.json: "
    "Dell EventService fixture"
)
DELL_EVIDENCE = {
    "query_select": DELL_SERVICE_ROOT_EVIDENCE,
    "query_filter": DELL_SERVICE_ROOT_EVIDENCE,
    "query_expand": DELL_SERVICE_ROOT_EVIDENCE,
    "query_top": DELL_SERVICE_ROOT_EVIDENCE,
    "query_only": DELL_SERVICE_ROOT_EVIDENCE,
    "one_query_param_per_uri": (
        "tests/test_query.py: Dell one-query-parameter gate coverage"
    ),
    "job_scheduling": DELL_JOB_SERVICE_EVIDENCE,
    "one_recurring_job_per_type": DELL_JOB_SERVICE_EVIDENCE,
    "schedulable_uris": DELL_JOB_SERVICE_EVIDENCE,
    "lifecycle_events_sse": DELL_EVENT_SERVICE_EVIDENCE,
}

DELL = register(
    VendorCapabilities(
        vendor="dell",
        oem_prefix="Dell",
        # iDRAC honors all five query parameters server-side...
        query_select=True,
        query_filter=True,
        query_expand=True,
        query_top=True,
        query_only=True,
        # ...but only one query parameter per URI is supported.
        one_query_param_per_uri=True,
        job_scheduling=True,
        one_recurring_job_per_type=True,
        schedulable_uris=DELL_SCHEDULABLE_URIS,
        lifecycle_events_sse=True,
        evidence=DELL_EVIDENCE,
    )
)
