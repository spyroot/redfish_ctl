"""Dual-mode tests for the log-clear command (LogService.ClearLog).

Uses the HPE overlay because it exposes several clearable log services across
both the Systems and Managers roots (IML/SL/Event on Systems/1, IEL on
Managers/1), which exercises the cross-vendor multi-root discovery. ClearLog is
DESTRUCTIVE, so the guard/confirm behavior is the core of these tests. Offline,
no live BMC.
"""
import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.logs.cmd_log_clear import LogClear
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType

_IML_CLEAR_TARGET = "/redfish/v1/Systems/1/LogServices/IML/Actions/LogService.ClearLog"


def _post_requests(redfish_service):
    """Return POST requests recorded by the mock Redfish service.

    :param redfish_service: the MockRedfishService recording requests.
    :return: list of recorded POST requests.
    """
    return [r for r in redfish_service.requests if r.method == "POST"]


def test_log_clear_lists_services_without_mutating(redfish_mock_factory):
    """With no --log-service the command lists clearable services and never POSTs."""
    mgr, svc = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(ApiRequestType.LogClear, "log-clear")

    assert isinstance(result, CommandResult)
    assert result.error is None
    ids = {s["Id"] for s in result.data["clearable_log_services"]}
    assert {"IML", "IEL"}.issubset(ids)
    assert _post_requests(svc) == []


def test_log_clear_dry_run_resolves_target_without_post(redfish_mock_factory):
    """--dry_run resolves the ClearLog target and classifies it, without POSTing."""
    mgr, svc = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(
        ApiRequestType.LogClear, "log-clear", log_service="IML", dry_run=True)

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["level"] == "destructive"
    assert result.data["target"] == _IML_CLEAR_TARGET
    assert _post_requests(svc) == []


def test_log_clear_without_confirm_is_blocked(redfish_mock_factory):
    """ClearLog is DESTRUCTIVE: without --confirm the guard blocks the POST."""
    mgr, svc = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(
        ApiRequestType.LogClear, "log-clear", log_service="IML")

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert _post_requests(svc) == []


def test_log_clear_confirm_posts_clearlog(redfish_mock_factory):
    """--confirm fires the ClearLog POST at the discovered target."""
    mgr, svc = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(
        ApiRequestType.LogClear, "log-clear", log_service="IML", confirm=True)

    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert result.data["target"] == _IML_CLEAR_TARGET

    posts = _post_requests(svc)
    assert len(posts) == 1
    assert posts[0].path.lower() == _IML_CLEAR_TARGET.lower()


def test_log_clear_resolves_full_uri(redfish_mock_factory):
    """--log-service accepts a full LogService URI (the Managers/1 IEL root)."""
    mgr, svc = redfish_mock_factory("hpe")

    result = mgr.sync_invoke(
        ApiRequestType.LogClear, "log-clear",
        log_service="/redfish/v1/Managers/1/LogServices/IEL", confirm=True)

    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == (
        "/redfish/v1/Managers/1/LogServices/IEL/Actions/LogService.ClearLog")


def test_log_clear_unknown_service_raises(redfish_mock_factory):
    """An unknown LogService Id fails fast with InvalidArgument, no POST."""
    mgr, svc = redfish_mock_factory("hpe")

    with pytest.raises(InvalidArgument, match="no clearable log service with Id 'Nope'"):
        mgr.sync_invoke(ApiRequestType.LogClear, "log-clear", log_service="Nope")

    assert _post_requests(svc) == []


def test_log_clear_no_clearable_services_raises(redfish_mock):
    """A box exposing no ClearLog action (the Dell mock) fails with a clear message."""
    with pytest.raises(
            InvalidArgument, match="no clearable log services found"):
        redfish_mock.sync_invoke(
            ApiRequestType.LogClear, "log-clear", log_service="Sel")


def test_resolve_target_matches_id_case_insensitively():
    """_resolve_target matches a LogService Id regardless of case."""
    services = [{"Id": "SEL", "uri": "/redfish/v1/Managers/1/LogServices/SEL"}]
    assert LogClear._resolve_target("sel", services) == (
        "/redfish/v1/Managers/1/LogServices/SEL")


def test_resolve_target_ambiguous_id_raises():
    """A LogService Id present under two roots is ambiguous and must be rejected."""
    services = [
        {"Id": "SEL", "uri": "/redfish/v1/Systems/1/LogServices/SEL"},
        {"Id": "SEL", "uri": "/redfish/v1/Managers/1/LogServices/SEL"},
    ]
    with pytest.raises(InvalidArgument, match="ambiguous"):
        LogClear._resolve_target("SEL", services)


def test_resolve_target_unknown_uri_raises():
    """A full URI that matches no discovered clearable service is rejected."""
    services = [{"Id": "SEL", "uri": "/redfish/v1/Managers/1/LogServices/SEL"}]
    with pytest.raises(
            InvalidArgument, match="no clearable log service at URI"):
        LogClear._resolve_target("/redfish/v1/Managers/1/LogServices/Nope", services)
