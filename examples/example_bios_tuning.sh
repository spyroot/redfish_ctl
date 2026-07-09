# Tune a BIOS attribute out-of-band, stage it, and reboot to apply
# Look the attribute up in the BIOS registry (valid values + current)
redfish_ctl bios-registry --attr_name MemTest
# Change it and apply on the next reset (-r reboots the host)
redfish_ctl bios-change --attr_name MemTest --attr_value Disabled on-reset -r
# Confirm the value once the host is back
redfish_ctl bios --filter MemTest
