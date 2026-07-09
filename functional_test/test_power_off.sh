#!/bin/bash

python redfish_ctl chassis --filter PowerState
python redfish_ctl chassis-reset --reset_type ForceOff
sleep 10
python redfish_ctl.py chassis --filter PowerState
