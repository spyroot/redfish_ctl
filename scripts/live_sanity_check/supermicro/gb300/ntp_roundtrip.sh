#!/opt/homebrew/bin/bash
# GB300 (NVIDIA HGX / OpenBMC) - ManagerNetworkProtocol NTP round-trip.
#
# Target/creds come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD env only.
# This script never touches the BMC except through `redfish_ctl`.
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}" "${REDFISH_USERNAME:?}" "${REDFISH_PASSWORD:?}"

RCTL=(redfish_ctl --nocolor --json_only)
CAPTURE_DIR="${TRACE_DIR:-scripts/live_sanity_check/captures/supermicro/gb300}"
TEST_SERVER="${1:-0.pool.ntp.org}"

mkdir -p "$CAPTURE_DIR"

select_ntp_state() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
for row in payload.get("data", []):
    ntp = row.get("NTP") or {}
    servers = ntp.get("NTPServers") or []
    if ntp.get("ProtocolEnabled") is not None or servers:
        print("{}\t{}".format(row.get("Manager", ""), json.dumps(servers)))
        break
else:
    raise SystemExit("FAIL: no NTP-capable manager found")
'
}

servers_for_manager() {
  local manager_id="$1"
  python3 -c '
import json
import sys

manager_id = sys.argv[1]
payload = json.load(sys.stdin)
for row in payload.get("data", []):
    if row.get("Manager") == manager_id:
        print(json.dumps((row.get("NTP") or {}).get("NTPServers") or []))
        break
else:
    raise SystemExit(f"FAIL: manager {manager_id!r} not found")
' "$manager_id"
}

json_array_from_args() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@"
}

assert_servers() {
  local manager_id="$1"
  local expected_json="$2"
  local label="$3"
  local current_json

  "${RCTL[@]}" manager-network > "$CAPTURE_DIR/ntp.${label}.read.json"
  current_json="$(
    servers_for_manager "$manager_id" < "$CAPTURE_DIR/ntp.${label}.read.json"
  )"
  if [ "$current_json" != "$expected_json" ]; then
    echo "FAIL: expected $expected_json after $label, got $current_json"
    exit 1
  fi
}

echo "[gb300] NTP round-trip on $REDFISH_IP"
"${RCTL[@]}" manager-network > "$CAPTURE_DIR/ntp.before.json"

state="$(select_ntp_state < "$CAPTURE_DIR/ntp.before.json")"
IFS=$'\t' read -r manager_id original_servers_json <<< "$state"

mapfile -t original_servers < <(
  python3 -c '
import json
import sys
for server in json.loads(sys.argv[1]):
    print(server)
' "$original_servers_json"
)

set_servers=("$TEST_SERVER")
if [ "$original_servers_json" = "$(json_array_from_args "$TEST_SERVER")" ]; then
  set_servers=("1.pool.ntp.org")
fi
set_servers_json="$(json_array_from_args "${set_servers[@]}")"

"${RCTL[@]}" ntp-set \
  --manager "$manager_id" \
  --server "${set_servers[0]}" \
  --confirm > "$CAPTURE_DIR/ntp.set.ok.json"
assert_servers "$manager_id" "$set_servers_json" "set"

if [ "${#original_servers[@]}" -eq 0 ]; then
  "${RCTL[@]}" ntp-set \
    --manager "$manager_id" \
    --clear \
    --confirm > "$CAPTURE_DIR/ntp.restore.ok.json"
else
  restore_args=()
  for server in "${original_servers[@]}"; do
    restore_args+=(--server "$server")
  done
  "${RCTL[@]}" ntp-set \
    --manager "$manager_id" \
    "${restore_args[@]}" \
    --confirm > "$CAPTURE_DIR/ntp.restore.ok.json"
fi
assert_servers "$manager_id" "$original_servers_json" "restore"

if "${RCTL[@]}" ntp-set \
    --manager "$manager_id" \
    --clear \
    --server "$TEST_SERVER" \
    --confirm > "$CAPTURE_DIR/ntp.err.json" 2>&1; then
  echo "FAIL: --clear with --server unexpectedly succeeded"
  exit 1
fi

echo "PASS: NTP servers round-tripped and restored for manager $manager_id"
