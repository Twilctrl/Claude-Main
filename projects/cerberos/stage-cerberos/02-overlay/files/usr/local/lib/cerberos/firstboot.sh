#!/bin/bash
# CerberOS first boot: check the Hailo-10H driver, then download the models
# and web apps that don't ship inside the image. Re-runs each boot until done.
set -u
log() { echo "[cerberos-firstboot] $*"; }

if [ ! -e /dev/hailo0 ]; then
    log "no /dev/hailo0; trying to load the Hailo driver"
    if ! modprobe hailo1x_pci 2>/dev/null && ! modprobe hailo_pci 2>/dev/null; then
        log "driver not loadable; building it for $(uname -r)"
        dkms autoinstall -k "$(uname -r)" &&
            { modprobe hailo1x_pci 2>/dev/null || modprobe hailo_pci 2>/dev/null; }
    fi
    udevadm settle
    if [ -e /dev/hailo0 ]; then
        systemctl restart hailo-ollama.service
    else
        log "still no /dev/hailo0 (is the AI HAT+ 2 fitted?). NPU models will wait; CPU models still work."
    fi
fi

if /usr/local/bin/cerb preload; then
    mkdir -p /var/lib/cerberos
    touch /var/lib/cerberos/firstboot.done
    log "done"
else
    log "some downloads failed; retrying next boot (or run: sudo cerb preload)"
fi
/usr/local/bin/cerb sync >/dev/null 2>&1 || true
exit 0
