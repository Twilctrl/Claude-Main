#!/bin/bash -e
# Install hailo-ollama, and make sure the Hailo-10H driver was built for the
# Pi 5 kernel. (DKMS runs inside the chroot, where `uname -r` is the build
# host's kernel, so build explicitly for each kernel that has headers.)

# hailo-ollama runs as its own unprivileged user. Its models live in its home.
if ! id hailo >/dev/null 2>&1; then
	useradd --system --home-dir /var/lib/hailo-ollama --create-home \
		--shell /usr/sbin/nologin --groups video hailo
fi

if [ -f /tmp/hailo-genai.deb ]; then
	apt-get install -y /tmp/hailo-genai.deb
	rm -f /tmp/hailo-genai.deb
	# Some releases keep the model store under /usr/share; let the service write it.
	if [ -d /usr/share/hailo-ollama ]; then
		chown -R hailo:hailo /usr/share/hailo-ollama
	fi
fi

for kdir in /lib/modules/*; do
	kver=$(basename "$kdir")
	if [ -d "$kdir/build" ]; then
		dkms autoinstall -k "$kver" || echo "dkms: build for $kver failed; hailcore-firstboot will retry"
	fi
done
