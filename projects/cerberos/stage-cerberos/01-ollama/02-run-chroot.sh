#!/bin/bash -e

if ! id ollama >/dev/null 2>&1; then
	useradd --system --home-dir /var/lib/ollama --create-home \
		--shell /usr/sbin/nologin --groups video,render ollama 2>/dev/null ||
	useradd --system --home-dir /var/lib/ollama --create-home \
		--shell /usr/sbin/nologin ollama
fi

if [ -f /tmp/ollama.tar.zst ]; then
	tar --zstd -xf /tmp/ollama.tar.zst -C /usr
	rm -f /tmp/ollama.tar.zst
else
	echo "ollama tarball missing; the CPU head will be absent (see README)"
fi

mkdir -p /var/lib/ollama/models
chown -R ollama:ollama /var/lib/ollama
