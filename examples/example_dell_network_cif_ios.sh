#!/usr/bin/env bash
set -euo pipefail

# Dell OEM network ISO boot through a CIFS share.
# Required environment:
#   CIFS_SERVER, CIFS_SHARE, CIFS_IMAGE, CIFS_USERNAME, CIFS_PASSWORD

: "${CIFS_SERVER:?set CIFS_SERVER}"
: "${CIFS_SHARE:?set CIFS_SHARE}"
: "${CIFS_IMAGE:?set CIFS_IMAGE}"
: "${CIFS_USERNAME:?set CIFS_USERNAME}"
: "${CIFS_PASSWORD:?set CIFS_PASSWORD}"

# Check whether media is already attached.
redfish_ctl oem-net-ios-status
redfish_ctl oem-attach-status

# Disconnect stale media before attaching a new image.
redfish_ctl oem-disconnect

# Attach the image.
redfish_ctl oem-attach \
  --ip_addr "$CIFS_SERVER" \
  --share_name "$CIFS_SHARE" \
  --remote_image "$CIFS_IMAGE" \
  --remote_username "$CIFS_USERNAME" \
  --remote_password "$CIFS_PASSWORD"

# Verify attach status.
redfish_ctl oem-net-ios-status

# Boot from the attached network ISO.
redfish_ctl oem-boot-netios \
  --ip_addr "$CIFS_SERVER" \
  --share_name "$CIFS_SHARE" \
  --remote_image "$CIFS_IMAGE" \
  --remote_username "$CIFS_USERNAME" \
  --remote_password "$CIFS_PASSWORD"

# Watch Dell OEM deployment task state.
redfish_ctl oem-net-iso-task
