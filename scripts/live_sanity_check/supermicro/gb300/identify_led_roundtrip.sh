#!/usr/bin/env bash
# GB300 identify LED round-trip, calls only redfish_ctl.
#
# Target and credentials come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD.
# Optional overrides:
#   IDENTIFY_LED_RESOURCE=chassis|system
#   IDENTIFY_LED_TARGET_ID=Chassis_0|System_0
#   IDENTIFY_LED_PROPERTY=LocationIndicatorActive|IndicatorLED
#   IDENTIFY_LED_CAPTURE_DIR=scripts/live_sanity_check/captures/supermicro/gb300
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}" "${REDFISH_USERNAME:?}" "${REDFISH_PASSWORD:?}"

RCTL=(redfish_ctl --nocolor --json_only)
RESOURCE="${IDENTIFY_LED_RESOURCE:-chassis}"
TARGET_ID="${IDENTIFY_LED_TARGET_ID:-Chassis_0}"
CAPTURE_DIR="${IDENTIFY_LED_CAPTURE_DIR:-scripts/live_sanity_check/captures/supermicro/gb300}"
PROPERTY_ARGS=()
RESTORE_NEEDED=0
RESTORE_FLAG=""

if [ -n "${IDENTIFY_LED_PROPERTY:-}" ]; then
  PROPERTY_ARGS=(--property "$IDENTIFY_LED_PROPERTY")
fi

mkdir -p "$CAPTURE_DIR"

json_get() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get(sys.argv[1], ""))' "$1"
}

read_state() {
  "${RCTL[@]}" identify-led \
    --resource "$RESOURCE" \
    --target-id "$TARGET_ID" \
    "${PROPERTY_ARGS[@]}"
}

apply_state() {
  local flag="$1"
  "${RCTL[@]}" identify-led \
    --resource "$RESOURCE" \
    --target-id "$TARGET_ID" \
    "${PROPERTY_ARGS[@]}" \
    "$flag" \
    --confirm
}

restore_on_failure() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$RESTORE_NEEDED" -eq 1 ]; then
    echo "WARN: attempting best-effort identify LED restore with $RESTORE_FLAG"
    if restored="$(apply_state "$RESTORE_FLAG" 2>&1)"; then
      printf '%s\n' "$restored" > "$CAPTURE_DIR/identify_led.restore.best_effort.json"
    else
      echo "WARN: best-effort restore failed"
      printf '%s\n' "$restored"
    fi
  fi
}
trap restore_on_failure EXIT

echo "[gb300] identify-led ${RESOURCE}/${TARGET_ID} -> ${REDFISH_IP}"
before="$(read_state)"
printf '%s\n' "$before" > "$CAPTURE_DIR/identify_led.read.json"

property="$(printf '%s' "$before" | json_get property)"
current="$(printf '%s' "$before" | json_get current)"
if [ "$property" = "LocationIndicatorActive" ]; then
  if [ "$current" = "True" ]; then
    set_flag="--off"
    restore_flag="--on"
    expected="False"
  else
    set_flag="--on"
    restore_flag="--off"
    expected="True"
  fi
else
  if [ "$current" = "Lit" ]; then
    set_flag="--off"
    restore_flag="--on"
    expected="Off"
  else
    set_flag="--on"
    restore_flag="--off"
    expected="Lit"
  fi
fi

RESTORE_FLAG="$restore_flag"
RESTORE_NEEDED=1

changed="$(apply_state "$set_flag")"
printf '%s\n' "$changed" > "$CAPTURE_DIR/identify_led.ok.json"
observed="$(printf '%s' "$changed" | json_get observed)"
if [ "$observed" != "$expected" ]; then
  echo "FAIL: expected observed=$expected after $set_flag, got '$observed'"
  printf '%s\n' "$changed"
  exit 1
fi

restored="$(apply_state "$restore_flag")"
printf '%s\n' "$restored" > "$CAPTURE_DIR/identify_led.restore.json"
restored_value="$(printf '%s' "$restored" | json_get observed)"
if [ "$restored_value" != "$current" ]; then
  echo "FAIL: expected restored observed=$current, got '$restored_value'"
  printf '%s\n' "$restored"
  exit 1
fi

RESTORE_NEEDED=0
echo "PASS: identify LED ${RESOURCE}/${TARGET_ID} changed to $observed and restored to $restored_value."
