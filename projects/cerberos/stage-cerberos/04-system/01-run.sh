#!/bin/bash -e
# Boot configuration for the Pi 5 + AI HAT+ 2.

BOOT="${ROOTFS_DIR}/boot/firmware"

if ! grep -q "cerberos" "${BOOT}/config.txt"; then
	cat >> "${BOOT}/config.txt" <<'CFG'

[pi5]
# --- cerberos ---
# PCIe Gen 3 doubles the bandwidth to the Hailo-10H.
dtparam=pciex1_gen=3
# No rainbow splash: straight to the hellfire console.
disable_splash=1
[all]
CFG
fi

# Quiet, themed boot. The vt.default_* values load the CerberOS hellfire palette
# into the kernel console before anything else prints.
VT_RED=$(sed -n 1p "${ROOTFS_DIR}/etc/cerberos/vtrgb")
VT_GRN=$(sed -n 2p "${ROOTFS_DIR}/etc/cerberos/vtrgb")
VT_BLU=$(sed -n 3p "${ROOTFS_DIR}/etc/cerberos/vtrgb")
if ! grep -q "vt.default_red" "${BOOT}/cmdline.txt"; then
	sed -i "1 s/\$/ quiet loglevel=3 logo.nologo consoleblank=0 vt.default_red=${VT_RED} vt.default_grn=${VT_GRN} vt.default_blu=${VT_BLU}/" "${BOOT}/cmdline.txt"
fi
