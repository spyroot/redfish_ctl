#!/usr/bin/env bash
set -euo pipefail

# Disable Memory Test and enable 4G MMIO, then verify after the host comes back.
redfish_ctl bios-registry --attr_name MemTest,MmioAbove4Gb
redfish_ctl bios-change \
  --attr_name MemTest,MmioAbove4Gb \
  --attr_value Disabled,Enabled \
  on-reset \
  --show
redfish_ctl bios-change \
  --attr_name MemTest,MmioAbove4Gb \
  --attr_value Disabled,Enabled \
  on-reset \
  -r
redfish_ctl --no_extra --no_action bios --filter MmioAbove4Gb,MemTest
