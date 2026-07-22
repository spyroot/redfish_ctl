"""Offline tests for RedfishManager.default_error_handler status mapping.

Regression for a tautology (`status_code >= 200 or status_code < 300`, true for
every int) that returned Success for 4xx/5xx and left the 401/403/404 branches
dead. These pin the success/raise mapping. No network.
"""
import pytest

from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.redfish_exceptions import (
    RedfishException,
    RedfishForbidden,
    RedfishUnauthorized,
)
from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.redfish_respond_error import RedfishError
from redfish_ctl.redfish_shared import RedfishApiRespond

_DMTF_ERROR_BODY = {
    "error": {
        "code": "Base.1.18.GeneralError",
        "message": "The write failed.",
        "@Message.ExtendedInfo": [
            {"MessageId": "Base.1.18.ActionNotSupported",
             "Message": "not supported", "Severity": "Critical"}],
    }
}


class _Resp:
    """Minimal fake response: a status code and an empty JSON body."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {}


class _WriteResp:
    """Fake write response: status code, headers, and a JSON error body."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.headers = {}
        self._body = body

    def json(self):
        return self._body


def _base_manager():
    """Return an offline IDracManager (the mutation-path host).

    :return: a IDracManager instance that makes no BMC contact.
    """
    return IDracManager(
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
        (400, RedfishException),
        (401, RedfishUnauthorized),
        (403, RedfishForbidden),
        (404, ResourceNotFound),
        (500, RedfishException),
        (502, RedfishException),
        (503, RedfishException),
    ],
)
def test_write_path_preserves_dmtf_envelope(status_code, exception):
    """read_api_respond (the POST/PATCH/DELETE path) raises the parsed RedfishError
    envelope for every error code, not a flattened string. Regression: 401/403 used
    'Authorization failed.', 5xx used '<message> HTTP Status code: N' (dropping
    error.code + @Message.ExtendedInfo), and 404 raised the wrong type."""
    with pytest.raises(exception) as raised:
        _base_manager().read_api_respond(_WriteResp(status_code, _DMTF_ERROR_BODY),
                                         expected=204)
    parsed = raised.value.args[0]
    assert isinstance(parsed, RedfishError)
    assert parsed.status_code == status_code
    assert parsed.code == "Base.1.18.GeneralError"


def test_write_path_405_returns_error_with_envelope():
    """405/409 keep returning RedfishApiRespond.Error (callers read the envelope
    from self._redfish_error), so the non-raising return contract is unchanged."""
    manager = _base_manager()
    result = manager.read_api_respond(_WriteResp(405, _DMTF_ERROR_BODY), expected=204)
    # compare by member name: the base uses idrac_shared.RedfishApiRespond,
    # a distinct enum from redfish_shared's, so identity comparison would fail.
    assert result.name == "Error"
    assert isinstance(manager._redfish_error, RedfishError)
    assert manager._redfish_error.status_code == 405
