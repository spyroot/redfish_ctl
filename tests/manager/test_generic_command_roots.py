"""Regression tests for product-neutral action and discovery command roots."""

import inspect

import pytest

from redfish_ctl.actions.cmd_action_list import ActionList
from redfish_ctl.component_integrity.cmd_component_integrity import (
    QueryComponentIntegrity,
)
from redfish_ctl.compute.cmd_system_reset import SystemReset
from redfish_ctl.discovery.cmd_bmc_scan import BmcScan
from redfish_ctl.discovery.cmd_discovery import Discovery
from redfish_ctl.environment.cmd_environment_metrics import EnvironmentMetrics
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.network.cmd_network_adapters import NetworkAdapters
from redfish_ctl.ports.cmd_nvlink_ports import NvLinkPorts
from redfish_ctl.redfish_api_common import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult, RedfishManager
from redfish_ctl.sensors.cmd_sensors import Sensors
from redfish_ctl.thermal.cmd_leak_detectors import LeakDetectors
from redfish_ctl.thermal.cmd_thermal import Thermal


GENERIC_COMMANDS = (
    (ApiRequestType.ActionList, "action_list", ActionList),
    (ApiRequestType.SystemReset, "system_reset", SystemReset),
    (ApiRequestType.BmcScan, "bmc-scan", BmcScan),
    (ApiRequestType.Discovery, "discovery", Discovery),
    (ApiRequestType.EnvironmentMetrics, "environment-metrics", EnvironmentMetrics),
    (ApiRequestType.Thermal, "thermal", Thermal),
    (ApiRequestType.Sensors, "sensors", Sensors),
    (ApiRequestType.NvLinkPorts, "nvlink-ports", NvLinkPorts),
    (ApiRequestType.LeakDetectors, "leak-detectors", LeakDetectors),
    (ApiRequestType.NetworkAdapters, "network-adapters", NetworkAdapters),
    (
        ApiRequestType.ComponentIntegrity,
        "component-integrity",
        QueryComponentIntegrity,
    ),
)


def test_idrac_init_does_not_redeclare_shared_connection_fields():
    """Dell initialization adds options without owning shared credentials."""
    redfish_params = inspect.signature(RedfishManager.__init__).parameters
    idrac_params = inspect.signature(IDracManager.__init__).parameters

    assert {"host", "username", "password"} <= redfish_params.keys()
    assert {"host", "username", "password"}.isdisjoint(idrac_params)
    assert "log_level" in idrac_params


def test_managed_system_discovery_is_owned_by_redfish_manager():
    """Canonical ManagerForServers discovery must not depend on Dell state."""
    assert "manager_uri" in RedfishManager.__dict__
    assert "managed_system_uri" in RedfishManager.__dict__
    assert "idrac_members" not in IDracManager.__dict__
    assert "idrac_manage_servers" not in IDracManager.__dict__


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
