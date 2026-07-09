#!/usr/bin/env bash
set -euo pipefail

# Read running jobs.
redfish_ctl jobs --running

# Read completed jobs.
redfish_ctl jobs --completed

# Watch one job until it reaches a terminal state.
redfish_ctl job-watch --job_id JID_746683021869

# Delete one approved job.
redfish_ctl job-rm --job_id JID_746683021869
