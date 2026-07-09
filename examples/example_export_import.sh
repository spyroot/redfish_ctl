#!/bin/bash

# export config
redfish_ctl system-export -f last_config.json

# check no scheduled jobs
redfish_ctl jobs --scheduled

# adjust something in last_config.json
# for example set MmioAbove4Gb to disabled if it enabled.
# now import
redfish_ctl system-import --config last_config.json --shutdown_type Forced -r

# verify value
redfish_ctl --no_extra --no_action bios --filter MmioAbove4Gb
