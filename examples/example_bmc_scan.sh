# Find every Redfish BMC on a network segment before you provision anything.
# Read-only host discovery: one unauthenticated GET /redfish/v1 per host, no
# credentials, no mutation. Use it to locate Dell/HPE/Supermicro/OpenBMC BMCs on
# a lab or rack subnet when you don't yet know their IPs.
idrac_ctl bmc-scan --subnet 192.168.254.0/24

# Tune the sweep for a big/slow segment:
#   --port     HTTPS port to probe (default 443)
#   --timeout  per-host seconds (bump to 3-5 for slow BMCs; default 2)
#   --workers  concurrent probes (default 64)
idrac_ctl bmc-scan --subnet 10.43.3.0/24 --timeout 4 --workers 128

# Each hit reports IP, RedfishVersion, Product, Vendor, and Auth. Auth=open means
# the ServiceRoot answered unauthenticated; Auth=required means the BMC is there
# but locks /redfish/v1 behind a login (a 401/403) — re-query it with creds:
#   export IDRAC_IP=<hit-ip> IDRAC_USERNAME=... IDRAC_PASSWORD=...
#   idrac_ctl system
