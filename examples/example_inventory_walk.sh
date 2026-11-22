# Quick read-only inventory of a server I just racked - no changes
# The big picture: model, serial, firmware
redfish_ctl --vendor dell system
# Chassis health and power state
redfish_ctl --vendor dell chassis
# All PCIe devices (NICs, GPUs, HBAs)
redfish_ctl --vendor dell pci
# Storage controllers and their status
redfish_ctl --vendor dell storage-list
# Physical drives behind the controllers
redfish_ctl --vendor dell storage-drives
# What boot mode and device is set right now
redfish_ctl --vendor dell current_boot
