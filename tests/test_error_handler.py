"""Offline tests for RedfishManager.default_error_handler status mapping.

Regression for a tautology (`status_code >= 200 or status_code < 300`, true for
every int) that returned Success for 4xx/5xx and left the 401/403/404 branches
dead. These pin the success/raise mapping. No network.
"""
import pytest

from redfish_ctl.cmd_exceptions import ResourceNotFound
from redfish_ctl.redfish_exceptions import RedfishForbidden, RedfishUnauthorized
from redfish_ctl.redfish_manager import RedfishManager
from redfish_ctl.redfish_shared import RedfishApiRespond


class _Resp:
    """Minimal fake response: a status code and an empty JSON body."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {}


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
