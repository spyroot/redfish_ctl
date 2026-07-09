# Read or fix the BMC (Manager) clock over Redfish. A drifted BMC RTC (common on
# boxes with no NTP, e.g. Supermicro X10 Redfish 1.0.1 stuck years in the past)
# skews every log/SEL timestamp — fix it before you trust the logs.

# Read each Manager's current DateTime (safe, no write):
redfish_ctl manager-time

# Set the BMC clock to this host's current UTC (a deliberate write):
redfish_ctl manager-time --now

# Or set an explicit time / local offset:
redfish_ctl manager-time --set 2026-07-02T20:00:00+00:00
redfish_ctl manager-time --now --offset +00:00

# Vendor-neutral: walks every Manager and PATCHes Managers/<id> {DateTime}.
# Reads by default; only --now/--set writes. If the BMC exposes NTP it's better
# to configure that, but thin/early Redfish often doesn't — then set it directly.
