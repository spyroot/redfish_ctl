"""Offline X10 coverage for Supermicro Node Manager policy actions."""

from copy import deepcopy

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType

NODE_MANAGER = "/redfish/v1/Systems/1/SmcNodeManager"
CLEAR_TARGET = f"{NODE_MANAGER}/Actions/SmcNodeManager.ClearAllPolicies"


def _post_requests(service):
    """Return POST requests captured by the mock Redfish transport."""
    return [request for request in service.requests if request.method == "POST"]


def test_smc_clear_policies_lists_x10_node_manager_without_post(redfish_mock_factory):
    """With no target, the command lists capable Node Managers and never POSTs."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcNodeManagerClearPolicies,
        "smc-clear-policies",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "node_managers": [{
            "Id": "Node Manager",
            "uri": NODE_MANAGER,
            "system": "/redfish/v1/Systems/1",
        }]
    }
    assert _post_requests(service) == []


def test_smc_clear_policies_accepts_string_oem_link(redfish_mock_factory):
    """Discovery accepts ``SmcNodeManager`` links encoded as plain URI strings."""
    manager, service = redfish_mock_factory("supermicro_x10")
    system = deepcopy(service._state("/redfish/v1/Systems/1"))
    system["Oem"]["Supermicro"]["SmcNodeManager"] = NODE_MANAGER
    service._overlay["/redfish/v1/Systems/1"] = system

    result = manager.sync_invoke(
        ApiRequestType.SmcNodeManagerClearPolicies,
        "smc-clear-policies",
    )

    assert result.error is None
    assert result.data == {
        "node_managers": [{
            "Id": "Node Manager",
            "uri": NODE_MANAGER,
            "system": "/redfish/v1/Systems/1",
        }]
    }


def test_smc_clear_policies_without_confirm_is_preview_only(redfish_mock_factory):
    """ClearAllPolicies resolves the action but does not POST without --confirm."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcNodeManagerClearPolicies,
        "smc-clear-policies",
        node_manager="Node Manager",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#SmcNodeManager.ClearAllPolicies"
    assert result.data["target"] == CLEAR_TARGET
    assert result.data["payload"] == {}
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(service) == []


def test_smc_clear_policies_confirm_posts_to_discovered_target(redfish_mock_factory):
    """--confirm POSTs to the action target advertised by the Node Manager."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcNodeManagerClearPolicies,
        "smc-clear-policies",
        node_manager=NODE_MANAGER,
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#SmcNodeManager.ClearAllPolicies"
    assert result.data["target"] == CLEAR_TARGET
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == CLEAR_TARGET.lower()
    assert posts[0].json() == {}


def test_smc_clear_policies_confirm_dry_run_still_does_not_post(redfish_mock_factory):
    """--dry_run remains a no-POST preview even when --confirm is supplied."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcNodeManagerClearPolicies,
        "smc-clear-policies",
        node_manager=NODE_MANAGER,
        confirm=True,
        dry_run=True,
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["target"] == CLEAR_TARGET
    assert _post_requests(service) == []


def test_smc_clear_policies_no_capable_node_manager_raises(redfish_mock_factory):
    """A non-X10 corpus fails clearly before any POST is attempted."""
    manager, service = redfish_mock_factory("hpe")

    with pytest.raises(InvalidArgument, match="no ClearAllPolicies-capable"):
        manager.sync_invoke(
            ApiRequestType.SmcNodeManagerClearPolicies,
            "smc-clear-policies",
            node_manager="Node Manager",
        )

    assert _post_requests(service) == []
