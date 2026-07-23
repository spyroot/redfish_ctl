"""Offline tests for the hoisted command registry and dispatch machinery.

The command ``_registry`` plus the ``invoke``/``sync_invoke``/``async_invoke``
dispatch used to live on the Dell child ``IDracManager``. They now live on the
product-neutral parent ``RedfishManager``; command classes inherit them one MRO
hop higher and behave exactly as before. These tests pin that: the registry is a
single shared object, dispatch still routes a known command, and an unknown
``api_call`` now raises ``UnsupportedAction`` (the ``defaultdict`` delta) instead
of a ``KeyError``. No BMC or network is involved.

Author Mus <spyroot@gmail.com>
"""
import pytest

from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult, RedfishManager

# Importing the command module registers SystemQuery/system_query via
# __init_subclass__, so the registry is populated regardless of collection order.
from redfish_ctl.system.cmd_system import SystemQuery  # noqa: F401


def test_registry_is_a_single_shared_object():
    """Child and parent expose the very same registry object, not two copies."""
    assert IDracManager._registry is RedfishManager._registry


def test_registry_is_populated_with_known_commands():
    """The inherited registry holds real commands (system_query is registered)."""
    registry = IDracManager().get_registry()
    assert registry, "command registry is unexpectedly empty"
    assert ApiRequestType.SystemQuery in registry
    assert registry[ApiRequestType.SystemQuery].get("system_query") is SystemQuery


def test_known_command_dispatches_through_inherited_sync_invoke(redfish_mock):
    """A known command still routes through the inherited machinery to a result."""
    result = redfish_mock.sync_invoke(ApiRequestType.SystemQuery, "system_query")
    assert isinstance(result, CommandResult)


def test_unknown_api_call_raises_unsupported_action_not_keyerror():
    """An unregistered api_call key hits the defaultdict path and raises cleanly.

    With the old ``{t: {} for t in ApiRequestType}`` registry this key was absent
    and produced a ``KeyError``; the ``defaultdict(dict)`` yields an empty map so
    the missing-name guard raises ``UnsupportedAction`` instead.
    """
    with pytest.raises(UnsupportedAction):
        IDracManager.invoke("no-such-api-kind", "system_query")


def test_unknown_name_under_known_api_call_raises_unsupported_action():
    """A valid api_call with an unregistered name raises ``UnsupportedAction``."""
    with pytest.raises(UnsupportedAction):
        IDracManager.invoke(ApiRequestType.SystemQuery, "not_a_registered_name")
