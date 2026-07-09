# Enable UEFI Secure Boot out-of-band and verify the key databases
# Current state + which key databases (PK/KEK/db/dbx) are populated
redfish_ctl secure-boot
# Find the exact BIOS attribute name for your platform (it varies by vendor)
redfish_ctl bios-registry --attr_name SecureBoot
# Turn it on; the change is pending and applies on the next reset (-r reboots)
redfish_ctl bios-change --attr_name SecureBoot --attr_value Enabled on-reset -r
# Confirm once the host is back
redfish_ctl secure-boot
