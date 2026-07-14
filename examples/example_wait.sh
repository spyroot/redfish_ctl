# Wait for the BMC Redfish service to be reachable instead of hand-rolled sleep loops.
# Any HTTP response (200/401/403) means the BMC is up; connection error means not yet.

# Wait up to 5 min for the BMC to answer:
redfish_ctl wait

# Tune the wait:
redfish_ctl wait --timeout 600 --interval 5

# After an operator-approved BMC restart, wait for the full down->up cycle:
redfish_ctl wait --reboot-cycle --timeout 300

# --reboot-cycle first waits for the BMC to go DOWN, then for it to come back UP,
# and reports went_down + reachable + waited_s. Vendor-neutral (Dell/HPE/Supermicro).
