#!/opt/homebrew/bin/bash
# GB300 (NVIDIA HGX / OpenBMC) - EventDestination subscription round-trip.
#
# Target/creds come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD env only.
# SUBSCRIPTION_DESTINATION must point at an operator-approved listener.
# This script never touches the BMC except through `redfish_ctl`.
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}" "${REDFISH_USERNAME:?}" "${REDFISH_PASSWORD:?}"
: "${SUBSCRIPTION_DESTINATION:?set SUBSCRIPTION_DESTINATION}"

RCTL=(redfish_ctl --nocolor --json_only)
CAPTURE_DIR="${TRACE_DIR:-scripts/live_sanity_check/captures/supermicro/gb300}"
CONTEXT="${SUBSCRIPTION_CONTEXT:-gb300-subscription-roundtrip}"

mkdir -p "$CAPTURE_DIR"

members_json() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
members = payload.get("data", {}).get("Subscriptions", {}).get("members") or []
print(json.dumps(members, sort_keys=True))
'
}

created_location() {
  python3 -c '
import json
import sys
from urllib.parse import urlparse

payload = json.load(sys.stdin)
location = payload.get("data", {}).get("location") or ""
if location.startswith("http://") or location.startswith("https://"):
    location = urlparse(location).path
print(location)
'
}

new_member() {
  python3 -c '
import json
import sys

before = set(json.loads(sys.argv[1]))
after = json.loads(sys.argv[2])
preferred = sys.argv[3]

if preferred and preferred in after:
    print(preferred)
    raise SystemExit(0)

created = [member for member in after if member not in before]
if created:
    print(created[-1])
    raise SystemExit(0)

raise SystemExit("FAIL: subscription member was not created")
' "$1" "$2" "$3"
}

contains_member() {
  python3 -c '
import json
import sys

members = set(json.loads(sys.argv[1]))
raise SystemExit(0 if sys.argv[2] in members else 1)
' "$1" "$2"
}

echo "[gb300] Event subscription round-trip on $REDFISH_IP"
"${RCTL[@]}" event-service > "$CAPTURE_DIR/subscription.before.json"
before_members="$(members_json < "$CAPTURE_DIR/subscription.before.json")"

"${RCTL[@]}" subscription-create \
  --destination "$SUBSCRIPTION_DESTINATION" \
  --event-format-type Event \
  --context "$CONTEXT" \
  --confirm > "$CAPTURE_DIR/subscription.create.ok.json"
location="$(created_location < "$CAPTURE_DIR/subscription.create.ok.json")"

created_member=""
for _ in 1 2 3 4 5; do
  "${RCTL[@]}" event-service > "$CAPTURE_DIR/subscription.after-create.json"
  after_members="$(members_json < "$CAPTURE_DIR/subscription.after-create.json")"
  if created_member="$(new_member "$before_members" "$after_members" "$location" 2>/dev/null)"; then
    break
  fi
  sleep 2
done

if [ -z "$created_member" ]; then
  echo "FAIL: subscription member was not created"
  exit 1
fi

"${RCTL[@]}" subscription-delete \
  --subscription "$created_member" \
  --confirm > "$CAPTURE_DIR/subscription.delete.ok.json"

for _ in 1 2 3 4 5; do
  "${RCTL[@]}" event-service > "$CAPTURE_DIR/subscription.after-delete.json"
  after_delete_members="$(members_json < "$CAPTURE_DIR/subscription.after-delete.json")"
  if ! contains_member "$after_delete_members" "$created_member"; then
    break
  fi
  sleep 2
done

if contains_member "$after_delete_members" "$created_member"; then
  echo "FAIL: subscription member still present after delete: $created_member"
  exit 1
fi

if "${RCTL[@]}" subscription-delete \
    --subscription "$created_member" \
    --confirm > "$CAPTURE_DIR/subscription.err.json" 2>&1; then
  echo "FAIL: deleting an already-deleted subscription unexpectedly succeeded"
  exit 1
fi

echo "PASS: subscription created and deleted: $created_member"
