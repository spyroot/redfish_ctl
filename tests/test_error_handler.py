"""Offline tests for RedfishManager.default_error_handler status mapping.

Regression for a tautology (`status_code >= 200 or status_code < 300`, true for
every int) that returned Success for 4xx/5xx and left the 401/403/404 branches
dead. These pin the success/raise mapping. No network.
"""
import pytest

from redfish_ctl.cmd_exceptions import (
    AuthenticationFailed,
    ResourceNotFound,
    UnexpectedResponse,
)
from redfish_ctl.redfish_exceptions import RedfishForbidden, RedfishUnauthorized
from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_respond_error import RedfishError
from redfish_ctl.redfish_shared import RedfishApiRespond

_DMTF_ERROR_BODY = {
    "error": {
        "code": "Base.1.18.GeneralError",
        "message": "Standard VirtualMedia is not implemented on this BMC.",
        "@Message.ExtendedInfo": [
            {"MessageId": "Base.1.18.ActionNotSupported",
             "Message": "The action is not supported.", "Severity": "Critical"}],
    }
}


class _Resp:
    """Minimal fake response: a status code and an empty JSON body."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {}


class _BodyResp:
    """Fake response carrying a status code and a JSON error body."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _base_manager():
    """Return an offline RedfishManagerBase — the class every command subclasses.

    :return: a RedfishManagerBase instance that makes no BMC contact.
    """
    return RedfishManagerBase(
        idrac_ip="mock", idrac_username="root", idrac_password="x",
        insecure=True, is_debug=False)


@pytest.mark.parametrize(
    "status_code, expected",
    [
        (200, RedfishApiRespond.Ok),
        (202, RedfishApiRespond.AcceptedTaskGenerated),
        (204, RedfishApiRespond.Success),
        (201, RedfishApiRespond.Success),
        (299, RedfishApiRespond.Success),
    ],
)
def test_success_codes(status_code, expected):
    """2xx codes map to the right RedfishApiRespond value."""
    assert RedfishManager.default_error_handler(_Resp(status_code)) == expected


@pytest.mark.parametrize(
    "status_code, exception",
    [
        (401, RedfishUnauthorized),
        (403, RedfishForbidden),
        (404, ResourceNotFound),
        (400, ResourceNotFound),
        (500, ResourceNotFound),
        (503, ResourceNotFound),
    ],
)
def test_error_codes_raise(status_code, exception):
    """4xx/5xx codes raise (the branches the tautology used to skip)."""
    with pytest.raises(exception):
        RedfishManager.default_error_handler(_Resp(status_code))


@pytest.mark.parametrize(
    "status_code, exception",
    [
        (401, AuthenticationFailed),
        (403, RedfishForbidden),
        (404, ResourceNotFound),
        (405, UnexpectedResponse),
        (409, UnexpectedResponse),
        (500, UnexpectedResponse),
        (501, UnexpectedResponse),
        (502, UnexpectedResponse),
        (503, UnexpectedResponse),
    ],
)
def test_base_handler_preserves_dmtf_envelope(status_code, exception):
    """RedfishManagerBase.default_error_handler — the override every command uses —
    raises the parsed RedfishError envelope (status, error.code, @Message.ExtendedInfo)
    for every error code, not a generic string. Regression: the override previously
    raised "Failed acquire result. Status code N" for 501/5xx, defeating the Redfish
    error contract that the parent-only test could not see.
    """
    with pytest.raises(exception) as raised:
        _base_manager().default_error_handler(_BodyResp(status_code, _DMTF_ERROR_BODY))
    parsed = raised.value.args[0]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == status_code
    assert parsed.code == "Base.1.18.GeneralError"


@pytest.mark.parametrize(
    "status_code, expected",
    [(200, "Ok"), (201, "Created"), (202, "AcceptedTaskGenerated"),
     (204, "Success"), (206, "Success")],
)
def test_base_handler_maps_success_codes(status_code, expected):
    """The base override maps 2xx codes; an unmapped 2xx returns Success (the
    line-689 tautology that made this branch meaningless is fixed)."""
    result = _base_manager().default_error_handler(_BodyResp(status_code, {}))
    assert result.name == expected
