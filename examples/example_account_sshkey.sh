# Authorize (or clear) an account's SSH public key — HPE iLO OEM (Oem.Hpe.SSHKeys).
# Dry-run by default; only --confirm writes. Enforces HPE's limits: RSA key, <=1366 bytes.

# Preview what would be sent (no write):
idrac_ctl account-import-sshkey --username test --key-file ~/.ssh/id_rsa.pub

# Authorize the key for user 'test':
idrac_ctl account-import-sshkey --username test --key-file ~/.ssh/id_rsa.pub --confirm

# Clear the account's authorized SSH keys:
idrac_ctl account-import-sshkey --username test --remove --confirm

# Caveat: this sends HPE's DOCUMENTED body (PATCH Oem.Hpe.SSHKeys). Acceptance is
# firmware-dependent — an iLO 5 on fw 2.96 rejects it (PropertyNotWritableOrUnknown)
# and its Web UI uses a different, undocumented path. Verify against your firmware.
# Deleting the account auto-purges its SSH keys.
