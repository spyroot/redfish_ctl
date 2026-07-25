"""Offline tests for the vendor-composed command registry machinery.

The command ``_registry`` plus the ``invoke``/``sync_invoke``/``async_invoke``
dispatch used to live on the Dell child ``IDracManager``. They now live on the
product-neutral parent ``RedfishManager``. Each vendor owns a distinct registry,
while ``get_registry`` composes the DMTF base with that vendor's commands. These
tests pin both halves of that contract. No BMC or network is involved.

Author Mus <spyroot@gmail.com>
"""
import pytest

from redfish_ctl.actions.cmd_action_list import ActionList
from redfish_ctl.cmd_exceptions import UnsupportedAction
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import RedfishManager


def test_vendor_registry_is_distinct_from_dmtf_registry():
    """Dell commands stay isolated from the product-neutral DMTF registry."""
    assert IDracManager._registry is not RedfishManager._registry


def test_dmtf_registry_is_populated_with_known_generic_command():
    """The neutral registry contains the generic action inventory command."""
    registry = RedfishManager.get_registry()
    assert registry, "command registry is unexpectedly empty"
    assert ApiRequestType.ActionList in registry
    assert registry[ApiRequestType.ActionList].get("action_list") is ActionList


def test_dell_registry_composes_dmtf_commands():
    """A vendor manager sees DMTF commands without sharing their registry."""
    registry = IDracManager.get_registry()
    assert registry[ApiRequestType.ActionList].get("action_list") is ActionList


def test_unknown_api_call_raises_unsupported_action_not_keyerror():
    """An unregistered api_call key hits the defaultdict path and raises cleanly.

    With the old ``{t: {} for t in ApiRequestType}`` registry this key was absent
    and produced a ``KeyError``; the ``defaultdict(dict)`` yields an empty map so
    the missing-name guard raises ``UnsupportedAction`` instead.
    """
    with pytest.raises(UnsupportedAction):
        RedfishManager.invoke("no-such-api-kind", "action_list")


def test_unknown_name_under_known_api_call_raises_unsupported_action():
    """A valid api_call with an unregistered name raises ``UnsupportedAction``."""
    with pytest.raises(UnsupportedAction):
        RedfishManager.invoke(ApiRequestType.ActionList, "not_a_registered_name")
