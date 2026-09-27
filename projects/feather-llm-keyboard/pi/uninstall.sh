#!/usr/bin/env bash
# Undo install.sh: stop the bridge and give tty1 back to the normal login prompt.
# Leaves hailo-ollama.service, the UART settings and /etc/default/llm-keyboard alone.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo $0" >&2
    exit 1
fi

systemctl disable --now llm-keyboard.service || true
rm -f /etc/systemd/system/llm-keyboard.service
rm -rf /opt/llm-keyboard
systemctl daemon-reload
systemctl enable --now getty@tty1.service
echo "Removed the llm-keyboard service; tty1 has a login prompt again."
