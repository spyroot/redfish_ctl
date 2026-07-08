# Provision a Supermicro host from an OS installer ISO over the BMC's virtual media,
# then boot it once in UEFI to run an (unattended) install — no physical disc, no KVM.
#
# How it works: the BMC presents a network-hosted ISO as a virtual CD (Supermicro's
# OEM CfgCD), you point one boot at that virtual CD, and reset. Two Supermicro
# specifics this recipe handles:
#   * Old X10-era BMCs negotiate only SMB1/NTLMv1, which modern file servers disable
#     by default — so the ISO must be served from an SMB share that still offers SMB1,
#     or the mount is accepted but the disc never actually inserts.
#   * These BMCs expose UEFI and Legacy boot as separate targets. Use UefiUsbCd to
#     boot the virtual CD in UEFI; on some boards the Legacy CD path fails to start.
#
# Set IDRAC_IP / IDRAC_USERNAME / IDRAC_PASSWORD to the BMC first (see examples/README.md).

SMB_HOST="192.168.1.10"                       # host serving the ISO over SMB (SMB1)
ISO_PATH="/share/ubuntu-live-server.iso"      # path of the ISO on that share
SMB_USER="iso"; SMB_PASS="isopass"            # share credentials

# 1) Mount the ISO as the BMC's virtual CD (Supermicro OEM CfgCD).
idrac_ctl vm-mount --host "$SMB_HOST" --path "$ISO_PATH" \
  --share_user "$SMB_USER" --share_pass "$SMB_PASS"

# 2) Confirm the media actually inserted. "mounted" only means the config was set;
#    an SMB1 negotiation failure sets it yet never mounts the disc, so check Inserted.
idrac_ctl vm-mount --status

# 3) Point the NEXT boot at the virtual CD in UEFI, one time only — so once the OS
#    is installed the host goes back to booting its disk without further action.
idrac_ctl boot-one-shot --device UefiUsbCd

# 4) Reset into the installer.
idrac_ctl system-reset --reset_type ForceRestart --confirm

# 5) After the install finishes and the host reboots into the new OS, release the
#    virtual media so the CD can't be booted again:
#    idrac_ctl vm-mount --unmount
