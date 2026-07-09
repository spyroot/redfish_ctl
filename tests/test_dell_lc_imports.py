"""Regression: the Dell LC command modules must import IDRAC_API.

They build URLs with IDRAC_API.DellLCService; a missing import raised
NameError: name 'IDRAC_API' is not defined at command execution time.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.dell_lc import cmd_dell_lc_api, cmd_dell_lc_rs
from redfish_ctl.idrac_shared import IDRAC_API


def test_dell_lc_modules_reference_idrac_api():
    """IDRAC_API is in scope in both Dell LC modules (no NameError)."""
    assert cmd_dell_lc_rs.IDRAC_API is IDRAC_API
    assert cmd_dell_lc_api.IDRAC_API is IDRAC_API
    assert IDRAC_API.DellLCService  # the attribute the modules use exists
