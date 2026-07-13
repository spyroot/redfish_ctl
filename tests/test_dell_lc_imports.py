"""Regression: the Dell LC command modules must import RedfishEndpoint.

They build URLs with RedfishEndpoint.DellLCService; a missing import raised
NameError: name 'RedfishEndpoint' is not defined at command execution time.

Author Mus spyroot@gmail.com
"""
from redfish_ctl.command_shared import RedfishEndpoint
from redfish_ctl.dell_lc import cmd_dell_lc_api, cmd_dell_lc_rs


def test_dell_lc_modules_reference_idrac_api():
    """RedfishEndpoint is in scope in both Dell LC modules (no NameError)."""
    assert cmd_dell_lc_rs.RedfishEndpoint is RedfishEndpoint
    assert cmd_dell_lc_api.RedfishEndpoint is RedfishEndpoint
    assert RedfishEndpoint.DellLCService  # the attribute the modules use exists
