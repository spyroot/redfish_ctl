"""Regression tests for product-neutral action and discovery command roots."""

import inspect

import pytest

from redfish_ctl.actions.cmd_action_list import ActionList
from redfish_ctl.discovery.cmd_bmc_scan import BmcScan
from redfish_ctl.discovery.cmd_discovery import Discovery
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult, RedfishManager


GENERIC_COMMANDS = (
    (ApiRequestType.ActionList, "action_list", ActionList),
    (ApiRequestType.BmcScan, "bmc-scan", BmcScan),
    (ApiRequestType.Discovery, "discovery", Discovery),
)


def test_idrac_init_does_not_redeclare_shared_connection_fields():
    """Dell initialization adds options without owning shared credentials."""
    redfish_params = inspect.signature(RedfishManager.__init__).parameters
    idrac_params = inspect.signature(IDracManager.__init__).parameters

    assert {"host", "username", "password"} <= redfish_params.keys()
    assert {"host", "username", "password"}.isdisjoint(idrac_params)
    assert "log_level" in idrac_params


@pytest.mark.parametrize("_api_type,_name,command_cls", GENERIC_COMMANDS)
def test_generic_command_inherits_redfish_manager_only(
    _api_type,
    _name,
    command_cls,
):
    """DMTF commands must not inherit Dell transport or job semantics."""
    assert command_cls.__mro__[1] is RedfishManager
    assert IDracManager not in command_cls.__mro__


@pytest.mark.parametrize("api_type,name,command_cls", GENERIC_COMMANDS)
def test_generic_dispatch_never_constructs_idrac_manager(
    monkeypatch,
    api_type,
    name,
    command_cls,
):
    """Neutral dispatch resolves and constructs commands without Dell internals."""
    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("generic dispatch constructed IDracManager")

    monkeypatch.setattr(IDracManager, "__init__", fail_if_constructed)
    monkeypatch.setattr(
        command_cls,
        "execute",
        lambda self, **_kwargs: CommandResult(
            command_cls.__name__, None, None, None
        ),
    )

    result = RedfishManager.invoke(
        api_type,
        name,
        host="mock-redfish",
        username="root",
        password="mock",
        port=443,
        insecure=True,
        is_http=False,
    )

    assert result.data == command_cls.__name__
