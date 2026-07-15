"""Web explorer for redfish_ctl.

Serves a tree of the tool's read-only commands; selecting one invokes the real
command through the tool's own registry (``sync_invoke``) against the configured
BMC and returns the result. No shell scripts, no ad-hoc HTTP — every Redfish read
goes through a registered redfish_ctl command.
"""
