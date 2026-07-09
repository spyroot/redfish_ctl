#!/usr/bin/env bash
set -euo pipefail

# Convert RAID-capable disks under one controller to non-RAID.
# This changes storage state; run it only on an approved non-production target.

# First list controllers.
redfish_ctl storage-controllers

# Then inspect drives under the controller you plan to change.
redfish_ctl storage-drives \
  --controller AHCI.Embedded.2-1 \
  --filter Drives,Volumes

# Convert disks under that controller. Use --exclude for disks you must not touch.
redfish_ctl storage-convert-noraid \
  --controller AHCI.Embedded.2-1 \
  --exclude Disk.Direct.0-0:AHCI.Embedded.2-1
