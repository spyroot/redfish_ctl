# Find every Redfish BMC on a network segment before you provision anything.
# Read-only host discovery: one unauthenticated GET /redfish/v1 per host, no
# credentials, no mutation. Use it to locate Dell/HPE/Supermicro/OpenBMC BMCs on
# a lab or rack subnet when you don't yet know their IPs.
#
# Two equivalent entry points (same engine) — neither needs REDFISH_IP or creds:
redfish_ctl bmc-scan --subnet 192.0.2.0/24
redfish_ctl discovery --network 192.0.2.0/24   # integrated under `discovery`

# Tune the sweep for a big/slow segment:
#   --port     HTTPS port to probe (default 443)
#   --timeout  per-host seconds (bump to 3-5 for slow BMCs; default 2)
#   --workers  concurrent probes (default 64)
redfish_ctl bmc-scan --subnet 198.51.100.0/24 --timeout 4 --workers 128

# Each hit reports IP, RedfishVersion, Product, Vendor, and Auth. Auth=open means
# the ServiceRoot answered unauthenticated; Auth=required means the BMC is there
# but locks /redfish/v1 behind a login (a 401/403) — re-query it with creds:
#   export REDFISH_IP=<hit-ip> REDFISH_USERNAME=... REDFISH_PASSWORD=...
#   redfish_ctl system
