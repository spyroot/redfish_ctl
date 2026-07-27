"""Async GET parity tests for the generic Redfish manager query path."""

import asyncio
import warnings

import pytest

import redfish_ctl.idrac_manager as idrac_manager_module
import redfish_ctl.redfish_manager as redfish_manager_module
from redfish_ctl.cmd_current_boot import GetCurrentBoot
from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_exceptions import RedfishForbidden, RedfishUnauthorized
from redfish_ctl.redfish_manager import CommandResult, RedfishManager
from redfish_ctl.redfish_respond_error import RedfishError

_DMTF_ERROR_BODY = {
    "error": {
        "code": "Base.1.18.GeneralError",
        "message": "async GET rejected",
    }
}


class _AsyncGetResponse:
    """Minimal completed response returned by the async transport."""

    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        """Return the canned response body."""
        return self._body


def _manager():
    """Return an offline generic manager with no discovery side effects."""
    return RedfishManager(
        host="async-query.test",
        username="root",
        password="mock",
        insecure=True,
        is_http=True,
    )


def _current_boot_manager():
    """Return an offline current_boot command with a fixed managed system URI."""
    manager = GetCurrentBoot(
        host="current-boot-async.test",
        username="root",
        password="mock",
        insecure=True,
        is_http=True,
    )
    manager.__dict__["managed_system_uri"] = "/redfish/v1/Systems/System.Embedded.1"
    return manager


def _stub_async_get(monkeypatch, manager, response):
    """Replace the executor-backed transport with one completed response."""
    calls = []

    async def fake_async_get(_loop, request, _headers):
        calls.append(request)
        return response

    monkeypatch.setattr(manager, "api_async_get_call", fake_async_get)
    return calls


def _stub_sync_get(monkeypatch, manager, response):
    """Replace the synchronous transport with one completed response."""
    calls = []

    def fake_get(request, _headers):
        calls.append(request)
        return response

    monkeypatch.setattr(manager, "api_get_call", fake_get)
    return calls


def test_event_loop_helper_creates_usable_loop_without_deprecation_warning(monkeypatch):
    real_new_event_loop = asyncio.new_event_loop
    real_set_event_loop = asyncio.set_event_loop
    created = []

    def record_new_event_loop():
        loop = real_new_event_loop()
        created.append(loop)
        return loop

    real_set_event_loop(None)
    monkeypatch.setattr(
        redfish_manager_module.asyncio,
        "new_event_loop",
        record_new_event_loop,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        loop = RedfishManager._event_loop()

    try:
        assert created == [loop]
        assert loop.run_until_complete(asyncio.sleep(0)) is None
    finally:
        loop.close()
        real_set_event_loop(None)


def test_event_loop_helper_reuses_installed_loop_without_deprecation_warning():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            resolved = RedfishManager._event_loop()
        assert resolved is loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_event_loop_helper_avoids_deprecated_policy_on_early_supported_python(
    monkeypatch,
):
    loop = asyncio.new_event_loop()

    monkeypatch.setattr(
        redfish_manager_module.asyncio,
        "get_event_loop_policy",
        lambda: pytest.fail("deprecated policy lookup must not run"),
    )
    monkeypatch.setattr(
        redfish_manager_module.asyncio,
        "get_event_loop",
        lambda: loop,
    )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            resolved = RedfishManager._event_loop()
        assert resolved is loop
    finally:
        loop.close()


def test_event_loop_helper_avoids_deprecated_policy_on_python_314(monkeypatch):
    loop = asyncio.new_event_loop()

    def reject_policy_lookup():
        raise AssertionError("deprecated policy lookup must not run on Python 3.14")

    monkeypatch.setattr(
        redfish_manager_module.asyncio,
        "get_event_loop_policy",
        reject_policy_lookup,
    )
    monkeypatch.setattr(
        redfish_manager_module.asyncio,
        "get_event_loop",
        lambda: loop,
    )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            resolved = RedfishManager._event_loop()
        assert resolved is loop
    finally:
        loop.close()


def test_base_query_async_2xx_returns_command_result_data(monkeypatch):
    """A successful async GET returns through the synchronous command surface."""
    manager = _manager()
    payload = {"Id": "RootService"}
    _stub_async_get(
        monkeypatch,
        manager,
        _AsyncGetResponse(200, payload, {"Allow": "GET"}),
    )

    result = manager.base_query("/redfish/v1/", do_async=True)

    assert isinstance(result, CommandResult)
    assert result.data == payload
    assert result.extra == "GET"
    assert result.error is None
    assert manager.query_counter == 1


@pytest.mark.parametrize(
    ("status_code", "exception"),
    [
        (401, RedfishUnauthorized),
        (403, RedfishForbidden),
        (404, ResourceNotFound),
        (500, ResourceNotFound),
    ],
)
def test_base_query_async_errors_match_default_error_handler(
    monkeypatch,
    status_code,
    exception,
):
    """Async GETs reject the same 4xx/5xx statuses as synchronous GETs."""
    manager = _manager()
    _stub_async_get(
        monkeypatch,
        manager,
        _AsyncGetResponse(status_code, _DMTF_ERROR_BODY),
    )

    with pytest.raises(exception) as raised:
        manager.base_query("/redfish/v1/Missing", do_async=True)

    parsed = raised.value.args[0]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == status_code
    assert parsed.code == "Base.1.18.GeneralError"
    assert manager.query_counter == 1

    sync_manager = _manager()
    _stub_sync_get(
        monkeypatch,
        sync_manager,
        _AsyncGetResponse(status_code, _DMTF_ERROR_BODY),
    )

    with pytest.raises(exception):
        sync_manager.base_query("/redfish/v1/Missing")

    assert sync_manager.query_counter == manager.query_counter


def test_current_boot_async_error_raises_before_returning_error_payload(monkeypatch):
    """current_boot --async rejects a non-2xx ComputerSystem response."""
    manager = _current_boot_manager()
    calls = _stub_async_get(
        monkeypatch,
        manager,
        _AsyncGetResponse(500, _DMTF_ERROR_BODY),
    )

    with pytest.raises(ResourceNotFound) as raised:
        manager.execute(do_async=True)

    parsed = raised.value.args[0]
    assert calls == [
        "http://current-boot-async.test/redfish/v1/Systems/System.Embedded.1"
    ]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == 500
    assert parsed.code == "Base.1.18.GeneralError"


def test_idrac_async_get_call_returns_resolved_response_and_forwards_timeout(monkeypatch):
    """Dell async GET returns the completed response and forwards the HTTP timeout."""
    manager = IDracManager(
        host="idrac-async-query.test",
        username="root",
        password="mock",
        insecure=True,
        is_http=True,
    )
    response = _AsyncGetResponse(200, {"Id": "DellService"})
    calls = []

    monkeypatch.setattr(idrac_manager_module, "http_timeout", lambda: 12.5)

    def fake_get(request, **kwargs):
        calls.append((request, kwargs))
        return response

    monkeypatch.setattr(idrac_manager_module.requests, "get", fake_get)

    loop = manager._event_loop()
    result = loop.run_until_complete(
        manager.api_async_get_call(
            loop,
            "http://idrac-async-query.test/redfish/v1/",
            {"Accept": "application/json"},
        )
    )

    assert result is response
    assert len(calls) == 1
    request, kwargs = calls[0]
    assert request == "http://idrac-async-query.test/redfish/v1/"
    assert kwargs["verify"] is False
    assert kwargs["auth"] == ("root", "mock")
    assert kwargs["timeout"] == 12.5
