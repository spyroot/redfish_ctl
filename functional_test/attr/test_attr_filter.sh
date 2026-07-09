#!/bin/bash
source ../cluster.env
python ../../redfish_ctl.py attr --deep --filter USBFront.1.Enable
