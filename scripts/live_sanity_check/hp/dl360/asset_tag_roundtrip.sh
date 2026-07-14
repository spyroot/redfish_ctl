#!/opt/homebrew/bin/bash
# HPE iLO DL360 AssetTag round-trip for Chassis/1 and Systems/1.
# Target/creds come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD env only.
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}" "${REDFISH_USERNAME:?}" "${REDFISH_PASSWORD:?}"

if [ "${ASSET_TAG_SANITY_CONFIRM:-}" != "YES" ]; then
  echo "SKIP: set ASSET_TAG_SANITY_CONFIRM=YES for approved live AssetTag mutation"
  exit 0
fi

RCTL=(redfish_ctl --nocolor --json_only)
CAPTURE_DIR="${TRACE_DIR:-scripts/live_sanity_check/captures/hp/dl360}"
NEW_TAG="${ASSET_TAG_SANITY_VALUE:-redfish-ctl-asset-tag-roundtrip}"
mkdir -p "$CAPTURE_DIR"

RESTORE_NEEDED=0
RESTORE_RESOURCE=""
RESTORE_TARGET_ID=""
RESTORE_TAG=""
RESTORE_FILE=""

restore_on_exit() {
  local status="$?"
  if [ "$RESTORE_NEEDED" = "1" ]; then
    RESTORE_NEEDED=0
    echo "RESTORE: restoring $RESTORE_RESOURCE/$RESTORE_TARGET_ID AssetTag after failure"
    if ! "${RCTL[@]}" asset-tag-set \
        --resource "$RESTORE_RESOURCE" \
        --target-id "$RESTORE_TARGET_ID" \
        --asset-tag "$RESTORE_TAG" \
        --confirm > "$RESTORE_FILE" 2>&1; then
      echo "WARN: restore command failed; inspect $RESTORE_FILE"
    fi
  fi
  exit "$status"
}
trap restore_on_exit EXIT

asset_tag_from() {
  python3 -c '
import json
import sys
payload = json.load(sys.stdin)
print(payload.get("data", {}).get("current", ""))
'
}

observed_tag_from() {
  python3 -c '
import json
import sys
payload = json.load(sys.stdin)
print(payload.get("data", {}).get("observed", ""))
'
}

assert_equal() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  if [ "$actual" != "$expected" ]; then
    echo "FAIL: $label expected [$expected], got [$actual]"
    return 1
  fi
}

roundtrip() {
  local resource="$1"
  local target_id="$2"
  local name="${resource}_${target_id}"
  local before_file="$CAPTURE_DIR/asset_tag_${name}.before.json"
  local set_file="$CAPTURE_DIR/asset_tag_${name}.set.ok.json"
  local restore_file="$CAPTURE_DIR/asset_tag_${name}.restore.ok.json"
  local restore_err_file="$CAPTURE_DIR/asset_tag_${name}.restore.err.json"
  local err_file="$CAPTURE_DIR/asset_tag_${name}.err.json"

  "${RCTL[@]}" asset-tag-set \
    --resource "$resource" \
    --target-id "$target_id" > "$before_file"
  local before
  before="$(asset_tag_from < "$before_file")"

  RESTORE_NEEDED=1
  RESTORE_RESOURCE="$resource"
  RESTORE_TARGET_ID="$target_id"
  RESTORE_TAG="$before"
  RESTORE_FILE="$restore_err_file"

  "${RCTL[@]}" asset-tag-set \
    --resource "$resource" \
    --target-id "$target_id" \
    --asset-tag "$NEW_TAG" \
    --confirm > "$set_file"
  assert_equal "$(observed_tag_from < "$set_file")" "$NEW_TAG" "$name set"

  "${RCTL[@]}" asset-tag-set \
    --resource "$resource" \
    --target-id "$target_id" \
    --asset-tag "$before" \
    --confirm > "$restore_file"
  assert_equal "$(observed_tag_from < "$restore_file")" "$before" "$name restore"
  RESTORE_NEEDED=0

  if "${RCTL[@]}" asset-tag-set \
      --resource "$resource" \
      --target-id "missing-$target_id" \
      --asset-tag "$NEW_TAG" \
      --confirm > "$err_file" 2>&1; then
    echo "FAIL: invalid AssetTag target unexpectedly succeeded for $name"
    exit 1
  fi
}

roundtrip chassis 1
roundtrip system 1

echo "PASS: AssetTag round-trip completed for Chassis/1 and Systems/1"
