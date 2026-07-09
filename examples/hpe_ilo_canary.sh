#!/usr/bin/env bash
#
# HPE iLO canary — prove redfish_ctl talks to a REAL HTTP Redfish service on a
# non-Dell box, with no hardware, using HPE's open-source iLO Redfish emulator
# (BSD-3, https://github.com/HewlettPackard/ilo-redfish-emulator). It ships iLO
# mockups for several ProLiant trees; we serve the DL380a (H100 NVL) one.
#
# Requires: docker + docker compose, git, and redfish_ctl installed.
# Everything here is READ-ONLY (system-reset runs as a dry-run without --confirm).
set -euo pipefail

WORK="${TMPDIR:-/tmp}/ilo-redfish-emulator"
PORT="${HPE_EMULATOR_PORT:-45678}"
TREE="${HPE_EMULATOR_TREE:-DL380a}"

# 1) fetch the emulator (once) and serve one iLO tree over HTTPS on $PORT
[ -d "$WORK" ] || git clone --depth 1 https://github.com/HewlettPackard/ilo-redfish-emulator "$WORK"
cd "$WORK"
EXTERNAL_PORT="$PORT" MOCKUP_FOLDER="$TREE" docker compose up -d
trap 'docker compose down' EXIT
sleep 5   # let the service come up

# 2) point redfish_ctl at it — the emulator uses root / root_password on localhost
export REDFISH_IP="127.0.0.1" REDFISH_PORT="$PORT"
export REDFISH_USERNAME="root" REDFISH_PASSWORD="root_password"
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

# 3) run vendor-neutral READ commands against the live HPE tree
redfish_ctl sensors                                   # chassis sensor readings
redfish_ctl network-adapters                          # NICs / DPUs
redfish_ctl metric-reports                            # TelemetryService values
redfish_ctl component-integrity                       # SPDM attestation state
redfish_ctl actions                                   # every action + its risk level
redfish_ctl system-reset --reset_type GracefulRestart # DRY-RUN preview (no --confirm)

echo "HPE iLO canary OK — redfish_ctl drove a live non-Dell Redfish service."
