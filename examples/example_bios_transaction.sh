# Transactional BIOS change: capture a restore point, apply, roll back if needed.
# This is the safety net for mutating a real box — snapshot BEFORE you change.

# 1) Capture the CURRENT values of exactly the attributes you are about to change
#    (change.json is your bios-change spec). rollback.json is the precise inverse.
idrac_ctl bios-snapshot --from_spec change.json -f rollback.json

# 2) Apply the change and reboot to commit it
idrac_ctl bios-change --from_spec change.json on-reset -r

# 3) If the box misbehaves, roll back to the captured restore point
idrac_ctl bios-change --from_spec rollback.json on-reset -r

# Variants:
#   idrac_ctl bios-snapshot --attr_name ProcCStates,LogicalProc -f rollback.json  # by name
#   idrac_ctl bios-snapshot -f full_restore.json                                  # every attribute
