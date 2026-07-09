# Quick read-only inventory of a server I just racked - no changes
# The big picture: model, serial, firmware
redfish_ctl system
# Chassis health and power state
redfish_ctl chassis
# All PCIe devices (NICs, GPUs, HBAs)
redfish_ctl pci
# Storage controllers and their status
redfish_ctl storage-list
# Physical drives behind the controllers
redfish_ctl storage-drives
# What boot mode and device is set right now
redfish_ctl current_boot
