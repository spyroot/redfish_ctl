"""Async GET parity tests for the generic Redfish manager query path."""

import pytest

from redfish_ctl.cmd_current_boot import GetCurrentBoot
from redfish_ctl.cmd_exceptions import ResourceNotFound
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
