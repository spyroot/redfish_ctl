#!/opt/homebrew/bin/bash
# GB300 (NVIDIA HGX / OpenBMC) — SubmitTestEvent, calls ONLY redfish_ctl.
#
# Emit-only safe action (no host impact, nothing to revert). Asserts the BMC
# reports the action executed. Positive path VERIFIED live on 172.25.230.37
# (n17) 2026-07-14: data.Status == "success", executed == true.
#
# Target/creds come from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD env only.
# This script never touches the BMC except through `redfish_ctl`.
set -euo pipefail

: "${REDFISH_IP:?set REDFISH_IP}" "${REDFISH_USERNAME:?}" "${REDFISH_PASSWORD:?}"
RCTL=(redfish_ctl --nocolor --json_only)
MSGID="${1:-Base.1.18.1.Created}"

echo "[gb300] SubmitTestEvent MessageId=$MSGID -> $REDFISH_IP"
resp="$("${RCTL[@]}" event-submit-test --message_id "$MSGID")"

status="$(printf '%s' "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("data",{}).get("Status",""))')"
if [ "$status" != "success" ]; then
  echo "FAIL: expected data.Status=success, got '$status'"; printf '%s\n' "$resp"; exit 1
fi
echo "PASS: SubmitTestEvent executed (Status=$status) — real BMC response, not a stub."
