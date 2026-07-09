# Bare-metal provision: point a fresh box at an installer ISO and boot it
# See where it boots today
redfish_ctl current_boot
# Eject whatever virtual media is mounted
redfish_ctl get_vm
redfish_ctl eject_vm --device_id 1
# Confirm it's clear
redfish_ctl get_vm
# Mount the installer ISO as a virtual CD
redfish_ctl insert_vm --uri_path http://10.241.7.99/ubuntu-22.04.1-desktop-amd64.iso --device_id 1
# Set one-time boot to the virtual CD and reboot so it boots the installer (-r reboots)
redfish_ctl boot-one-shot --device Cd -r
