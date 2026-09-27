#!/usr/bin/env bash
# Set the Pi up to run the LLM keyboard bridge automatically at power-on.
#
#   sudo ./install.sh
#
# What it does:
#   - installs llm_bridge.py to /opt/llm-keyboard
#   - turns the UART on and the serial login console off (for the Feather link)
#   - starts hailo-ollama at boot, if it isn't already set up to
#   - gives the Pi's first console (tty1) to the bridge instead of a login prompt
# Re-running it is safe; it keeps your /etc/default/llm-keyboard settings.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo $0" >&2
    exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)
RUN_USER=${SUDO_USER:-pi}
INSTALL_DIR=/opt/llm-keyboard

echo "==> Installing the bridge to $INSTALL_DIR"
if ! python3 -c "import serial" 2>/dev/null; then
    apt-get install -y python3-serial
fi
install -D -m 755 "$HERE/llm_bridge.py" "$INSTALL_DIR/llm_bridge.py"
if [[ ! -f /etc/default/llm-keyboard ]]; then
    install -m 644 "$HERE/systemd/llm-keyboard.default" /etc/default/llm-keyboard
fi
install -m 644 "$HERE/systemd/llm-keyboard.service" /etc/systemd/system/llm-keyboard.service

echo "==> Enabling the UART (hardware on, serial login console off)"
if command -v raspi-config >/dev/null; then
    raspi-config nonint do_serial_hw 0 || echo "   (couldn't enable UART hardware; use raspi-config)"
    raspi-config nonint do_serial_cons 1 || echo "   (couldn't disable serial console; use raspi-config)"
else
    echo "   raspi-config not found; enable the UART manually"
fi

echo "==> Making the LLM server start at boot"
if systemctl cat hailo-ollama.service >/dev/null 2>&1; then
    echo "   hailo-ollama.service already exists; enabling it"
    systemctl enable hailo-ollama.service
elif HAILO_OLLAMA=$(command -v hailo-ollama); then
    sed -e "s|@USER@|$RUN_USER|" -e "s|@HAILO_OLLAMA@|$HAILO_OLLAMA|" \
        "$HERE/systemd/hailo-ollama.service.in" > /etc/systemd/system/hailo-ollama.service
    systemctl daemon-reload
    systemctl enable hailo-ollama.service
    echo "   installed hailo-ollama.service (runs $HAILO_OLLAMA as $RUN_USER)"
else
    echo "   hailo-ollama not found on PATH. Make sure your LLM server starts at"
    echo "   boot and set its URL in /etc/default/llm-keyboard."
fi

echo "==> Giving tty1 to the bridge"
systemctl daemon-reload
systemctl disable getty@tty1.service
systemctl enable llm-keyboard.service

cat <<EOF

Done. Reboot to start everything:  sudo reboot

After boot, the Pi's green LED shows the bridge's state:
  heartbeat blink = starting up / waiting,  solid = ready,  fast blink = typing

Settings:  /etc/default/llm-keyboard   Logs:  journalctl -u llm-keyboard -f
EOF
