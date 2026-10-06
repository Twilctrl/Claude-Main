#!/bin/bash -e
# The NPU head: Hailo-10H driver (built for the Pi 5 kernel) and hailo-ollama.
# DKMS runs inside the chroot, where `uname -r` is the build host's kernel, so
# build explicitly for each kernel that has headers.

if ! id hailo >/dev/null 2>&1; then
	useradd --system --home-dir /var/lib/hailo-ollama --create-home \
		--shell /usr/sbin/nologin --groups video hailo
fi

if [ -f /tmp/hailo-genai.deb ]; then
	apt-get install -y /tmp/hailo-genai.deb
	rm -f /tmp/hailo-genai.deb
	# The service runs /usr/bin/hailo-ollama; link it there if the package didn't.
	if [ ! -x /usr/bin/hailo-ollama ]; then
		found=$(command -v hailo-ollama || find /usr /opt -name hailo-ollama -type f -perm -u+x 2>/dev/null | head -n1)
		[ -n "$found" ] && ln -sf "$found" /usr/bin/hailo-ollama
	fi
	# Some releases keep the model store under /usr/share; let the service write it.
	if [ -d /usr/share/hailo-ollama ]; then
		chown -R hailo:hailo /usr/share/hailo-ollama
	fi
fi

for kdir in /lib/modules/*; do
	kver=$(basename "$kdir")
	if [ -d "$kdir/build" ]; then
		dkms autoinstall -k "$kver" || echo "dkms: build for $kver failed; cerberos-firstboot will retry"
	fi
done
