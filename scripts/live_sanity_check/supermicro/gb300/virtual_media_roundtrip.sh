#!/opt/homebrew/bin/bash
# GB300 / OpenBMC VirtualMedia mount-eject round trip.
#
# Target and credentials come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD
# env only. The only tool that talks to the BMC is redfish_ctl.
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}"
: "${REDFISH_USERNAME:?set REDFISH_USERNAME}"
: "${REDFISH_PASSWORD:?set REDFISH_PASSWORD}"
: "${VM_IMAGE_URI:?set VM_IMAGE_URI to an ISO URI reachable by the BMC}"

RCTL=(redfish_ctl --nocolor --json_only)
DEVICE_ID="${VM_DEVICE_ID:-USB1}"
CAPTURE_DIR="${CAPTURE_DIR:-scripts/live_sanity_check/captures/supermicro/gb300}"
OK_CAPTURE="$CAPTURE_DIR/virtual_media.ok.json"
ERR_CAPTURE="$CAPTURE_DIR/virtual_media.err.json"

mkdir -p "$CAPTURE_DIR"

json_get() {
  local expr="$1"
  jq -r "$expr"
}

capture() {
  local file="$1"
  local step="$2"
  local command="$3"
  local output="$4"
  jq -n \
    --arg step "$step" \
    --arg command "$command" \
    --argjson output "$output" \
    '{step: $step, command: $command, output: $output}' >> "$file"
}

read_device() {
  "${RCTL[@]}" get_vm --device_id "$DEVICE_ID"
}

restore_original() {
  set +e
  if [ "${ORIGINAL_INSERTED:-false}" = "true" ] && [ -n "${ORIGINAL_IMAGE:-}" ] &&
     [ "$ORIGINAL_IMAGE" != "null" ]; then
    "${RCTL[@]}" insert_vm --device_id "$DEVICE_ID" --uri_path "$ORIGINAL_IMAGE" --eject >/dev/null
  else
    "${RCTL[@]}" eject_vm --device_id "$DEVICE_ID" >/dev/null
  fi
}
trap restore_original EXIT

assert_original_restored() {
  local restored="$1"
  local restored_inserted
  local restored_image

  restored_inserted="$(printf '%s' "$restored" | json_get '.data.Inserted // false')"
  restored_image="$(printf '%s' "$restored" | json_get '.data.Image // ""')"

  if [ "$ORIGINAL_INSERTED" = "true" ] && [ "$restored_image" = "$ORIGINAL_IMAGE" ]; then
    return
  fi
  if [ "$ORIGINAL_INSERTED" != "true" ] && [ "$restored_inserted" = "false" ]; then
    return
  fi

  echo "FAIL: original VirtualMedia state was not restored" >&2
  printf '%s\n' "$restored" >&2
  exit 1
}

echo "[gb300] VirtualMedia round trip on device $DEVICE_ID -> $REDFISH_IP"
before="$(read_device)"
ORIGINAL_INSERTED="$(printf '%s' "$before" | json_get '.data.Inserted // false')"
ORIGINAL_IMAGE="$(printf '%s' "$before" | json_get '.data.Image // ""')"

: > "$OK_CAPTURE"
: > "$ERR_CAPTURE"
capture "$OK_CAPTURE" "read-before" "redfish_ctl get_vm --device_id $DEVICE_ID" "$before"

inserted="$(printf '%s' "$before" | json_get '.data.Inserted // false')"
if [ "$inserted" = "true" ]; then
  echo "[gb300] Existing media detected; it will be restored on exit."
fi

insert_out="$("${RCTL[@]}" insert_vm --device_id "$DEVICE_ID" --uri_path "$VM_IMAGE_URI" --eject)"
capture \
  "$OK_CAPTURE" \
  "insert" \
  "redfish_ctl insert_vm --device_id $DEVICE_ID --uri_path <VM_IMAGE_URI> --eject" \
  "$insert_out"

after_insert="$(read_device)"
capture "$OK_CAPTURE" "read-after-insert" "redfish_ctl get_vm --device_id $DEVICE_ID" "$after_insert"
after_inserted="$(printf '%s' "$after_insert" | json_get '.data.Inserted // false')"
after_image="$(printf '%s' "$after_insert" | json_get '.data.Image // ""')"
if [ "$after_inserted" != "true" ] || [ "$after_image" != "$VM_IMAGE_URI" ]; then
  echo "FAIL: expected inserted media image '$VM_IMAGE_URI'" >&2
  printf '%s\n' "$after_insert" >&2
  exit 1
fi

if [ "${RUN_NEGATIVE:-0}" = "1" ]; then
  set +e
  negative_out="$("${RCTL[@]}" insert_vm --device_id "__invalid__" --uri_path "$VM_IMAGE_URI" 2>&1)"
  negative_rc=$?
  set -e
  jq -n \
    --arg step "insert-invalid-device" \
    --arg command "redfish_ctl insert_vm --device_id __invalid__ --uri_path <VM_IMAGE_URI>" \
    --arg output "$negative_out" \
    --argjson exit_code "$negative_rc" \
    '{step: $step, command: $command, exit_code: $exit_code, output: $output}' >> "$ERR_CAPTURE"
  if [ "$negative_rc" -eq 0 ]; then
    echo "FAIL: invalid device insert unexpectedly succeeded" >&2
    exit 1
  fi
fi

eject_out="$("${RCTL[@]}" eject_vm --device_id "$DEVICE_ID")"
capture "$OK_CAPTURE" "eject" "redfish_ctl eject_vm --device_id $DEVICE_ID" "$eject_out"

after_eject="$(read_device)"
capture "$OK_CAPTURE" "read-after-eject" "redfish_ctl get_vm --device_id $DEVICE_ID" "$after_eject"
after_ejected="$(printf '%s' "$after_eject" | json_get '.data.Inserted // false')"
if [ "$after_ejected" != "false" ]; then
  echo "FAIL: expected media to be ejected" >&2
  printf '%s\n' "$after_eject" >&2
  exit 1
fi

restore_original
restored="$(read_device)"
capture "$OK_CAPTURE" "read-after-restore" "redfish_ctl get_vm --device_id $DEVICE_ID" "$restored"
assert_original_restored "$restored"
trap - EXIT

echo "PASS: VirtualMedia mounted, verified, ejected, restored, and verified."
