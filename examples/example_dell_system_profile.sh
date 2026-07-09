# Swap in a Dell PowerEdge System Profile — one attribute sets a whole power/perf policy.
# The available values differ across generations (iDRAC9 vs iDRAC10 / PowerEdge gen),
# so always list what YOUR box supports first:
redfish_ctl bios-registry --attr_name SysProfile
# Classic SysProfile values (14G/15G, iDRAC9):
#   PerfOptimized | PerfPerWattOptimizedOs | PerfPerWattOptimizedDapc | DenseCfgOptimized | Custom
redfish_ctl bios-change --attr_name SysProfile --attr_value PerfOptimized on-reset -r

# Newer PowerEdge (iDRAC10 / later gen) also expose a WorkloadProfile with turnkey
# policies including a low-latency one — confirm the exact value name first:
redfish_ctl bios-registry --attr_name WorkloadProfile
redfish_ctl bios-change --attr_name WorkloadProfile --attr_value LowLatencyOptimizedProfile on-reset -r

# Pick "Custom" as the base when you want to override individual knobs afterward
# (see example_low_latency_profile.sh).
