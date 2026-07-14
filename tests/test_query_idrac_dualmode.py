"""Dual-mode tests for the raw query command."""
import argparse
import json
from urllib.parse import unquote, urlsplit

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_main import create_cmd_tree
from redfish_ctl.redfish_manager import CommandResult

SYSTEM_RESOURCE = "/redfish/v1/Systems/System.Embedded.1"
MANAGERS_RESOURCE = "/redfish/v1/Managers"


def _assert_system_resource(result):
    """Assert the raw query returned the requested ComputerSystem resource."""
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == SYSTEM_RESOURCE
    assert result.data["Id"] == "System.Embedded.1"


def _assert_manager_collection(result):
    """Assert the raw query returned the requested Manager collection."""
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == MANAGERS_RESOURCE
    assert result.data["Members"][0]["@odata.id"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1"
    )


def test_query_idrac_returns_requested_resource(redfish_api):
    """query_idrac GETs the caller-provided Redfish resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryIdrac,
        "query_idrac",
        resource=SYSTEM_RESOURCE,
    )

    _assert_system_resource(result)


def test_get_command_is_registered_with_positional_uri():
    """get registers as an operator-friendly alias for raw URI reads."""
    parser = argparse.ArgumentParser()
    commands = create_cmd_tree(parser)

    assert "get" in commands
    parsed = parser.parse_args(["get", SYSTEM_RESOURCE])
    assert parsed.uri == SYSTEM_RESOURCE


def test_raw_get_returns_requested_resource(redfish_api):
    """raw_get GETs the caller-provided Redfish resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.RawGet,
        "raw_get",
        uri=SYSTEM_RESOURCE,
    )

    _assert_system_resource(result)


def test_query_idrac_returns_manager_collection(redfish_api):
    """query_idrac can fetch the Manager collection resource directly."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryIdrac,
        "query_idrac",
        resource=MANAGERS_RESOURCE,
    )

    _assert_manager_collection(result)


def test_query_idrac_expanded_sends_expand_query_in_mock_mode(
    redfish_mock,
    redfish_service,
):
    """query_idrac with do_expanded=True sends $expand on the raw path."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.QueryIdrac,
        "query_idrac",
        resource=SYSTEM_RESOURCE,
        do_expanded=True,
    )

    _assert_system_resource(result)
    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower() == SYSTEM_RESOURCE.lower()

    raw_query = request.query or urlsplit(request.url).query
    assert unquote(raw_query) == "$expand=*($levels=1)"
