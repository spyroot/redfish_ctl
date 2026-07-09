#!/bin/bash

redfish_ctl chassis --filter PowerState
redfish_ctl chassis-reset --reset_type On
sleep 10
python redfish_ctl.py chassis --filter PowerState
