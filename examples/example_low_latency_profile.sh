# Apply a low-latency / real-time BIOS profile from a spec file, in one shot.
# The spec (specs/realtime.opt.spec.json) turns off the jitter sources:
#   ProcCStates=Disabled  (no deep C-states)      MemTest=Disabled  (faster POST)
#   OsWatchdogTimer=Disabled                       MemFrequency=MaxPerf
#   SriovGlobalEnable=Enabled  (for passthrough/DPDK workloads)
# Preview exactly what will be sent, changing nothing:
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset --show
# Stage the whole profile and reboot to apply (-r):
redfish_ctl bios-change --from_spec specs/realtime.opt.spec.json on-reset -r
# Confirm a couple of knobs after the host is back:
redfish_ctl bios --filter ProcCStates
