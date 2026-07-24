"""Regression: the Dell LC command modules must import REDFISH_API.

They build URLs with REDFISH_API.DellLCService; a missing import raised
NameError: name 'REDFISH_API' is not defined at command execution time.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.dell_lc import cmd_dell_lc_api, cmd_dell_lc_rs
from redfish_ctl.idrac_shared import REDFISH_API


def test_dell_lc_modules_reference_idrac_api():
    """REDFISH_API is in scope in both Dell LC modules (no NameError)."""
    assert cmd_dell_lc_rs.REDFISH_API is REDFISH_API
    assert cmd_dell_lc_api.REDFISH_API is REDFISH_API
    assert REDFISH_API.DellLCService  # the attribute the modules use exists
