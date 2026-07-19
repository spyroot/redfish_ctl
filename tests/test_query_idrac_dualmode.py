"""Dual-mode tests for the raw query command."""
import argparse
import json
from urllib.parse import unquote, urlsplit

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager_shared import ApiRequestType
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


def test_raw_get_trims_requested_redfish_resource(redfish_api):
    """raw_get trims whitespace before issuing a Redfish resource read."""
    result = redfish_api.sync_invoke(
        ApiRequestType.RawGet,
        "raw_get",
        uri=f"  {MANAGERS_RESOURCE}  ",
    )

    _assert_manager_collection(result)


def test_raw_get_preserves_safe_query_string(redfish_mock, redfish_service):
    """raw_get keeps an inline Redfish query string on the requested path."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.RawGet,
        "raw_get",
        uri=f"{MANAGERS_RESOURCE}?$top=1",
    )

    _assert_manager_collection(result)
    request = redfish_service.last_request
    assert request.method == "GET"
    assert request.path.lower() == MANAGERS_RESOURCE.lower()

    raw_query = request.query or urlsplit(request.url).query
    assert unquote(raw_query) == "$top=1"


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("", "requires a /redfish/v1 resource URI"),
        ("https://example.invalid/redfish/v1/Managers", "not absolute URLs"),
        ("//example.invalid/redfish/v1/Managers", "not absolute URLs"),
        ("/redfish/v1/../Managers", "path traversal"),
        ("/redfish/v1/%2e%2e/Managers", "path traversal"),
        ("/redfish/v1/%252e%252e/Managers", "path traversal"),
        ("/redfish/v1/Managers%2f..%2fSystems", "path traversal"),
        ("/redfish/v1/Managers#top", "must not include a fragment"),
        ("/api/v1/Managers", "must start with /redfish/v1"),
        ("redfish/v1/Managers", "must start with /redfish/v1"),
        (r"/redfish/v1\\Managers", "forward slashes"),
        ("/redfish/v1/Managers%5cSystem", "forward slashes"),
    ],
)
def test_raw_get_rejects_non_resource_paths(redfish_api, uri, message):
    """raw_get fails closed before querying non-resource paths."""
    with pytest.raises(InvalidArgument, match=message):
        redfish_api.sync_invoke(
            ApiRequestType.RawGet,
            "raw_get",
            uri=uri,
        )


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
