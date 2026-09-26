# boot.py -- runs once at power-up, before code.py.
#
# Present the Feather to the laptop as a plain USB keyboard only (no mouse /
# consumer-control), and mark it as a "boot" keyboard so it also works in
# BIOS/UEFI menus and on hosts with minimal HID support.
#
# Changes to boot.py only take effect after a hard reset (RESET button or
# unplug/replug), not after a soft reload.

import usb_hid

usb_hid.enable((usb_hid.Device.KEYBOARD,), boot_device=1)

# Optional: hide the CIRCUITPY drive and the serial console from the laptop so
# it only sees a keyboard. Leave this disabled while you are developing, or you
# will have to use safe mode to get the drive back.
#
# import storage, usb_cdc
# storage.disable_usb_drive()
# usb_cdc.disable()
