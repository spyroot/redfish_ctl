#!/bin/bash
source ../device/device.env
python redfish_ctl.py chassis --filter PowerState
