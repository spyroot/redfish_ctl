"""Dual-mode tests for the log-clear command (LogService.ClearLog).

Uses the HPE overlay because it exposes several clearable log services across
both the Systems and Managers roots (IML/SL/Event on Systems/1, IEL on
Managers/1), which exercises the cross-vendor multi-root discovery. The Dell
lens runs against the XR8620t corpus, whose only clearable service is the SEL
at Managers/iDRAC.Embedded.1 (Lclog and FaultList expose no ClearLog); a Dell
ClearLog realizes with a ``JID_`` OEM job id. ClearLog is DESTRUCTIVE, so the
guard/confirm behavior is the core of these tests. Offline, no live BMC.
"""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.logs.cmd_log_clear import LogClear
from redfish_ctl.redfish_manager import CommandResult

DELL_CORPUS = corpus_dir(
    Path(__file__).parent.parent / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)
_IML_CLEAR_TARGET = "/redfish/v1/Systems/1/LogServices/IML/Actions/LogService.ClearLog"
_DELL_SEL = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel"
_DELL_SEL_CLEAR_TARGET = f"{_DELL_SEL}/Actions/LogService.ClearLog"


@pytest.fixture
def dell_log_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    The vendor-faithful service realizes an Action POST the Dell way: 202 plus
    a ``JID_`` OEM job id in the Location header, never a DMTF-generic token.

    :return: tuple of IDracManager and the recording MockRedfishService.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-logs",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


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


def test_log_clear_dell_lens_discovers_sel_only(dell_log_mock):
    """Dell-lens discovery lists exactly the corpus-proven clearable SEL.

    The XR8620t corpus carries three Manager log services (Sel, Lclog,
    FaultList); only the SEL exposes #LogService.ClearLog.
    """
    mgr, svc = dell_log_mock

    result = mgr.sync_invoke(ApiRequestType.LogClear, "log-clear")

    assert isinstance(result, CommandResult)
    assert result.error is None
    services = result.data["clearable_log_services"]
    assert [(s["Id"], s["uri"]) for s in services] == [("Sel", _DELL_SEL)]
    assert _post_requests(svc) == []


def test_log_clear_dell_lens_confirm_clears_sel(dell_log_mock):
    """--confirm clears the Dell SEL; the realization carries a ``JID_`` job id."""
    mgr, svc = dell_log_mock

    result = mgr.sync_invoke(
        ApiRequestType.LogClear, "log-clear", log_service="Sel", confirm=True)

    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["target"] == _DELL_SEL_CLEAR_TARGET
    assert result.data["task_id"] == svc.JOB_ID
    assert svc.JOB_ID.startswith("JID_")

    posts = _post_requests(svc)
    assert len(posts) == 1
    assert posts[0].path.lower() == _DELL_SEL_CLEAR_TARGET.lower()


def test_log_clear_no_clearable_services_raises(dell_log_mock):
    """A tree with the SEL's ClearLog stripped fails with a clear message.

    The Dell corpus proves the SEL IS clearable, so the no-clearable edge is
    constructed here by removing the action, never asserted as Dell semantics.
    A box can genuinely lack ClearLog everywhere, which is why the command
    fails closed with this message rather than a lookup error.
    """
    mgr, svc = dell_log_mock
    body = copy.deepcopy(svc._state(_DELL_SEL))
    body["Actions"].pop("#LogService.ClearLog")
    svc._overlay[_DELL_SEL] = body
    svc._overlay[_DELL_SEL.lower()] = body

    with pytest.raises(
            InvalidArgument, match="no clearable log services found"):
        mgr.sync_invoke(
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
