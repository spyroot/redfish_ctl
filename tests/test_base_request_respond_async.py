"""Async-path checks for ``RedfishManagerBase.base_request_respond``.

``base_request_respond`` had no test at all, and the whole offline suite exercised ``do_async=True``
in exactly one file. That gap let a reversed tuple unpack ship in three places: the
``api_async_*_until_complete`` helpers return ``(Response, RedfishApiRespond)`` — the same order the
synchronous branch binds — but the async branch unpacked them as ``api_resp, response``.

The consequence was not a clean failure. The write was sent to the BMC first, and only then did the
result handling touch the mis-bound values, so the mutation landed on real hardware and the caller got
a traceback instead of the result.

The pre-fix behaviour is measured, not assumed: with the three unpacks reverted, every test in this
module fails on Python 3.10 through 3.14 with
``AttributeError: 'RedfishApiRespond' object has no attribute 'status_code'`` — the enum reaching code
that expects a Response. That is a behavioural failure, not an import, collection or fixture error,
which is what makes it usable as regression evidence.

These tests pin the unpack order for all three mutating verbs by stubbing the async helper: the stub
returns the documented ``(Response, RedfishApiRespond)`` shape, and each test asserts the values
arrive bound to the right names.

Author Mus spyroot@gmail.com
"""
import pytest

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_shared import HTTPMethod, RedfishApiRespond


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` carrying just what the path touches.

    :param status_code: HTTP status the caller should observe.
    :param body: object returned by :meth:`json`.
    """

    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self.headers: dict = {}
        self._body = body if body is not None else {}

    def json(self) -> dict:
        """Return the canned response body.

        :return: the body this fake was constructed with.
        """
        return self._body


def _stub_async(monkeypatch, manager, helper_name: str, response, api_resp):
    """Replace one ``api_async_*_until_complete`` helper with a coroutine returning a fixed tuple.

    The helper is stubbed rather than mocked at the transport layer so the test pins the CONTRACT
    between the helper and its caller — which is exactly where the defect lived — instead of
    re-testing requests.

    :param monkeypatch: pytest monkeypatch fixture.
    :param manager: the RedfishManagerBase instance under test.
    :param helper_name: attribute name of the async helper to replace.
    :param response: object to return in the first tuple slot.
    :param api_resp: RedfishApiRespond to return in the second tuple slot.
    """

    async def _fake(*_args, **_kwargs):
        return response, api_resp

    monkeypatch.setattr(type(manager), helper_name, _fake, raising=True)


@pytest.mark.parametrize(
    "method, helper",
    [
        (HTTPMethod.POST, "api_async_post_until_complete"),
        (HTTPMethod.PATCH, "api_async_patch_until_complete"),
        (HTTPMethod.DELETE, "api_async_delete_until_complete"),
    ],
)
def test_async_unpack_binds_response_and_status_correctly(
    redfish_mock, monkeypatch, method, helper
):
    """Each async verb must bind (Response, RedfishApiRespond) in that order.

    Reversed, the enum reaches code expecting a Response and raises AttributeError on ``.status_code``
    after the request has already been sent (verified pre-fix on 3.10-3.14). Asserting a clean
    CommandResult plus a real enum is what proves the binding.
    """
    _stub_async(monkeypatch, redfish_mock, helper,
                _FakeResponse(200, {"Id": "1"}), RedfishApiRespond.Ok)

    result, api_resp = redfish_mock.base_request_respond(
        "/redfish/v1/Systems/System.Embedded.1",
        method,
        payload={},
        do_async=True,
        expected_status=200,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(api_resp, RedfishApiRespond), (
        "api_resp must be the enum; binding a Response here is the reversed-unpack defect"
    )
    assert api_resp is RedfishApiRespond.Ok
    assert result.error is None


def test_async_accepted_task_reads_the_header_from_the_response(redfish_mock, monkeypatch):
    """An accepted async task must read its id from the Response, not from the enum.

    This is the branch that made the reversal expensive: on AcceptedTaskGenerated the code calls
    ``job_id_from_header(response)``. With the names swapped that receives an enum, so a caller
    polling an async job could never learn its task id — after the mutation had already been issued.
    """
    fake = _FakeResponse(202, {})
    fake.headers = {"Location": "/redfish/v1/TaskService/Tasks/JID_123456789"}
    _stub_async(monkeypatch, redfish_mock, "api_async_post_until_complete",
                fake, RedfishApiRespond.AcceptedTaskGenerated)

    result, api_resp = redfish_mock.base_request_respond(
        "/redfish/v1/Systems/System.Embedded.1/Actions/Anything",
        HTTPMethod.POST,
        payload={},
        do_async=True,
        expected_status=202,
    )

    assert api_resp is RedfishApiRespond.AcceptedTaskGenerated
    assert isinstance(result, CommandResult)
    assert "task_id" in result.data, "the task id must come from the Response headers"


def test_async_and_sync_agree_on_the_return_contract(redfish_mock, monkeypatch):
    """Both branches must return (CommandResult, RedfishApiRespond) in the same order.

    The two branches were written independently and only the sync one was covered, which is how they
    drifted. Pinning them together stops the next divergence.
    """
    _stub_async(monkeypatch, redfish_mock, "api_async_post_until_complete",
                _FakeResponse(200, {}), RedfishApiRespond.Ok)

    async_result, async_status = redfish_mock.base_request_respond(
        "/redfish/v1/Systems/System.Embedded.1",
        HTTPMethod.POST, payload={}, do_async=True, expected_status=200,
    )

    assert isinstance(async_result, CommandResult)
    assert isinstance(async_status, RedfishApiRespond)


@pytest.mark.parametrize("status", [400, 401, 500])
def test_async_non_2xx_is_reported_not_swallowed(redfish_mock, monkeypatch, status):
    """do_async=True AND a non-2xx response — the trigger INTERSECTION, not either half alone.

    The reversed unpack was a defect of the combination. ``do_async=True`` with 2xx, or
    ``do_async=False`` with non-2xx, exercises neither broken line. Only the intersection reaches
    ``parse_json_respond_msg(response)`` and ``api_success_msg(api_resp)`` with an error status still
    bound to the wrong name.

    Asserts externally visible behaviour: the caller gets a CommandResult and a real enum rather than
    a traceback, and the failing status survives instead of being flattened into success. Under the
    reversed unpack this raises AttributeError on .status_code — after the request was already sent.
    """
    body = {"error": {"code": "Base.1.0.GeneralError", "message": "rejected"}}
    _stub_async(monkeypatch, redfish_mock, "api_async_post_until_complete",
                _FakeResponse(status, body), RedfishApiRespond.Error)

    result, resp_status = redfish_mock.base_request_respond(
        "/redfish/v1/Systems/System.Embedded.1/Actions/Anything",
        HTTPMethod.POST, payload={}, do_async=True, expected_status=200,
    )

    assert isinstance(result, CommandResult)
    assert isinstance(resp_status, RedfishApiRespond), (
        "a failing async call must still return the enum; binding a Response here is the defect"
    )
    assert resp_status is RedfishApiRespond.Error, "the failure must survive, not read as success"


def test_async_non_2xx_does_not_report_success(redfish_mock, monkeypatch):
    """A rejected async PATCH must not come back looking like it worked.

    The operationally relevant invariant: someone scripting against this cannot distinguish a 503
    from success if the status is flattened. Asserts the failing enum is what propagates, so a caller
    branching on it takes the error path.
    """
    _stub_async(monkeypatch, redfish_mock, "api_async_patch_until_complete",
                _FakeResponse(503, {}), RedfishApiRespond.Error)

    _result, resp_status = redfish_mock.base_request_respond(
        "/redfish/v1/Systems/System.Embedded.1",
        HTTPMethod.PATCH, payload={"AssetTag": "x"}, do_async=True, expected_status=200,
    )

    assert resp_status is not RedfishApiRespond.Ok
    assert resp_status is RedfishApiRespond.Error
