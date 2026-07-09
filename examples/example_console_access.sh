# See what console access the BMC exposes. Redfish DESCRIBES console access
# (connect types, sessions) but does not stream it — you reach the live console
# out of band using the reported connect types.
redfish_ctl console-info

# SerialConsole -> reach it over SOL (Serial Over LAN), e.g.:
#   ipmitool -I lanplus -H "$REDFISH_IP" -U "$REDFISH_USERNAME" -P "$REDFISH_PASSWORD" sol activate
#   ssh "$REDFISH_USERNAME@$REDFISH_IP"      # then start SOL from the BMC shell
# GraphicalConsole (KVMIP) -> open the BMC's HTML5/Java KVM viewer in a browser.
