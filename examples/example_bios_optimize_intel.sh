# Intel Xeon performance/power tuning (attribute names from an Intel BIOS registry)
# ALWAYS confirm the exact names + allowable values on your platform first:
redfish_ctl bios-registry --attr_name WorkloadProfile
# Max performance: turbo unthrottled, power capping off, VT-d on for passthrough
redfish_ctl bios-change --attr_name EnergyEfficientTurbo   --attr_value Disabled on-reset
redfish_ctl bios-change --attr_name DynamicPowerCapping    --attr_value Disabled on-reset
redfish_ctl bios-change --attr_name IntelUpiPowerManagement --attr_value Disabled on-reset
redfish_ctl bios-change --attr_name IntelProcVtd           --attr_value Enabled  on-reset -r
# Some platforms expose a single workload profile instead of the knobs above:
redfish_ctl bios-change --attr_name WorkloadProfile --attr_value MaximumPerformance on-reset -r
