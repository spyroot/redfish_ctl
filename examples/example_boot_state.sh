# "What will this box boot?" — infer boot target, order, and staged media
# before (re)provisioning, without opening the console.
idrac_ctl boot-state

# It reports: BootMode (UEFI/Legacy), the next boot target (a one-time override
# if pending, else the top of BootOrder), the full boot order resolved to names,
# every bootable entry, and any VirtualMedia currently mounted. Pair it with
# boot-one-shot / insert_vm to stage an install, then re-check boot-state.
