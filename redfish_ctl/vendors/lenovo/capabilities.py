"""Lenovo XClarity Controller capability profile.

Lenovo XCC documents query support through ServiceRoot.ProtocolFeaturesSupported.
Job scheduling and lifecycle event streaming stay conservative until pinned by a
captured fixture or emulator-backed test.

Author Mus spyroot@gmail.com
"""
from ..base import VendorCapabilities, register

LENOVO = register(
    VendorCapabilities(
        vendor="lenovo",
        oem_prefix="Lenovo",
        query_select=True,
        query_filter=True,
        query_expand=True,
        query_only=True,
        query_top=False,
        one_query_param_per_uri=False,
        job_scheduling=False,
        one_recurring_job_per_type=False,
        lifecycle_events_sse=False,
    )
)
