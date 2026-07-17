"""Regression: boot_source_registry must not build a double-slash path.

cmd_boot_source_registry joined ``{idrac_manage_servers}/{BootSourcesRegistryQuery}``,
but BootSourcesRegistryQuery already starts with ``/`` — producing
``/redfish/v1/Systems/System.Embedded.1//BootSources/BootSourcesRegistry``, which
404s. The constant is an absolute suffix, so callers must join WITHOUT an extra
slash.
"""
from redfish_ctl.redfish_manager_shared import REDFISH_API


def test_boot_sources_registry_query_is_absolute_suffix():
    """The query constant starts with '/' (it is a path suffix, not a fragment)."""
    assert REDFISH_API.BootSourcesRegistryQuery.startswith("/")


def test_boot_sources_registry_path_has_no_double_slash():
    """Joining the system path with the constant yields a single-slash path."""
    base = "/redfish/v1/Systems/System.Embedded.1"
    path = f"{base}{REDFISH_API.BootSourcesRegistryQuery}"
    assert "//" not in path
    assert path == "/redfish/v1/Systems/System.Embedded.1/BootSources/BootSourcesRegistry"
