#!/bin/bash -e

systemctl enable hailo-ollama.service cerberos-firstboot.service \
	cerberos-perf.service cerberos-palette.service

# Nothing on this box needs these, and each one steals CPU or SD-card I/O.
for unit in triggerhappy.service ModemManager.service cups.service \
	apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
	systemctl disable "$unit" 2>/dev/null || true
done

# Compressed swap in RAM instead of a swap file on the SD card. Recent
# Raspberry Pi OS already does this with rpi-swap; otherwise set it up.
if ! dpkg -s rpi-swap >/dev/null 2>&1; then
	apt-get install -y systemd-zram-generator
	cat > /etc/systemd/zram-generator.conf <<'ZRAM'
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
ZRAM
	apt-get purge -y dphys-swapfile 2>/dev/null || true
fi

# The login banner comes from /etc/update-motd.d/10-cerberos.
rm -f /etc/update-motd.d/10-uname
: > /etc/motd

# Every Ollama client on the system talks to the gate.
echo 'OLLAMA_HOST=127.0.0.1:11434' >> /etc/environment

# pi-gen leaves root with the password "root". Lock it; use sudo.
passwd -l root
