#!/bin/sh
# Start hailo-ollama wherever its package put it. Exits 0 (no restart loop) if
# it isn't installed yet; `hc doctor` explains how to add it.
set -e

bin=$(command -v hailo-ollama || true)
for p in /usr/bin/hailo-ollama /usr/local/bin/hailo-ollama /opt/hailo/bin/hailo-ollama; do
    [ -n "$bin" ] && break
    [ -x "$p" ] && bin=$p
done
if [ -z "$bin" ]; then
    echo "hailo-ollama is not installed. Install the Hailo GenAI Model Zoo .deb, then: sudo systemctl restart hailo-ollama" >&2
    exit 0
fi

if [ "${HAILCORE_LAN_API:-0}" = "1" ]; then
    # hailo-ollama binds 127.0.0.1:8000; expose it on the LAN with a socat relay.
    if command -v socat >/dev/null; then
        socat TCP-LISTEN:8001,fork,reuseaddr TCP:127.0.0.1:8000 &
        echo "LAN API relay listening on port 8001" >&2
    fi
fi

exec "$bin"
