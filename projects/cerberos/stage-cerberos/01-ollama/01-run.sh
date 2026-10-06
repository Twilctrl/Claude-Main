#!/bin/bash -e
# The CPU head: stage Ollama and any models build.sh baked, for the chroot step.

if [ -f files/ollama-linux-arm64.tar.zst ]; then
	install -m 644 files/ollama-linux-arm64.tar.zst "${ROOTFS_DIR}/tmp/ollama.tar.zst"
fi
if [ -d files/models ]; then
	mkdir -p "${ROOTFS_DIR}/var/lib/ollama/models"
	cp -a files/models/. "${ROOTFS_DIR}/var/lib/ollama/models/"
fi
