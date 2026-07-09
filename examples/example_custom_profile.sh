# Build and apply your OWN BIOS profile from a JSON spec — repeatable across a fleet.
# A spec is just {"Attributes": { "<name>": "<value>", ... }} (see specs/*.spec.json).
# 1) Author it once. Confirm each attribute name/value against the registry:
redfish_ctl bios-registry --attr_name ProcCStates
cat > /tmp/my_profile.spec.json <<'JSON'
{
  "Attributes": {
    "SysProfile": "Custom",
    "ProcCStates": "Disabled",
    "ProcC1E": "Disabled",
    "ProcTurboMode": "Enabled",
    "LogicalProc": "Disabled"
  }
}
JSON
# 2) Preview what would be sent (no change):
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset --show
# 3) Stage the profile and reboot to apply:
redfish_ctl bios-change --from_spec /tmp/my_profile.spec.json on-reset -r
