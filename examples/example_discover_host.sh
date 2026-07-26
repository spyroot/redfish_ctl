# Quick discovery of an unknown host - what is it and what does it expose
# Classify the vendor and walk the Redfish tree
redfish_ctl discovery
# System info: model, serial, firmware
redfish_ctl --vendor dell system
# Chassis: power, thermal, health
redfish_ctl --vendor dell chassis
# PCI devices to spot add-in cards (NICs, GPUs, HBAs)
redfish_ctl --vendor dell pci
# Storage controllers and attached drives
redfish_ctl --vendor dell storage-list
