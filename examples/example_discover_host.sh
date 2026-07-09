# Quick discovery of an unknown host - what is it and what does it expose
# Classify the vendor and walk the Redfish tree
redfish_ctl discovery
# System info: model, serial, firmware
redfish_ctl system
# Chassis: power, thermal, health
redfish_ctl chassis
# PCI devices to spot add-in cards (NICs, GPUs, HBAs)
redfish_ctl pci
# Storage controllers and attached drives
redfish_ctl storage-list
