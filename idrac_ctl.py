"""Main entry for idrac_ctl

The main interface consumed is iDRAC Manager class.
Each command registered dynamically and dispatch to respected execute method
by invoking request from IDRAC Manager.

Author Mus spyroot@gmail.com
"""
from idrac_ctl.redfish_main import redfish_main_ctl
if __name__ == "__main__":
    redfish_main_ctl()
