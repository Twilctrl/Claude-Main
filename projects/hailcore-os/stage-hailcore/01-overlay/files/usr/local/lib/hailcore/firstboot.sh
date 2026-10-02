#!/bin/bash
# Runs once at first boot (and again at later boots until it succeeds).
set -u
log() { echo "[hailcore-firstboot] $*"; }

URL=${HAILCORE_LLM_URL:-http://127.0.0.1:8000}
MODEL=${HAILCORE_MODEL:-qwen2.5-instruct:1.5b}

# 1. Driver. If the image's DKMS build didn't take (e.g. the kernel was
#    upgraded before first boot), build it now for the running kernel.
if [ ! -e /dev/hailo0 ]; then
    log "no /dev/hailo0; trying to load the Hailo driver"
    modprobe hailo1x_pci 2>/dev/null || modprobe hailo_pci 2>/dev/null || {
        log "driver not loadable; running dkms autoinstall for $(uname -r)"
        dkms autoinstall -k "$(uname -r)" && { modprobe hailo1x_pci 2>/dev/null || modprobe hailo_pci 2>/dev/null; }
    }
    udevadm settle
    if [ ! -e /dev/hailo0 ]; then
        log "still no /dev/hailo0. Is the AI HAT+ 2 seated, and is this a Pi 5? See: hc doctor"
        exit 0
    fi
    systemctl restart hailo-ollama.service
fi

# 2. Model.
if ! systemctl is-enabled --quiet hailo-ollama.service || ! command -v hailo-ollama >/dev/null; then
    log "hailo-ollama is not installed; skipping model download (see: hc doctor)"
    exit 0
fi
for _ in $(seq 120); do
    curl -fsS -m 2 "$URL/api/tags" >/dev/null 2>&1 && break
    sleep 2
done
if ! curl -fsS -m 2 "$URL/api/tags" >/dev/null 2>&1; then
    log "LLM server at $URL never came up; will retry next boot"
    exit 0
fi

if curl -fsS "$URL/api/tags" | jq -e --arg m "$MODEL" '.models[]? | select(.name == $m or .model == $m)' >/dev/null; then
    log "$MODEL already present"
else
    log "pulling $MODEL (this can take a while)"
    if ! curl -fsS -m 2400 "$URL/api/pull" -H 'Content-Type: application/json' \
            -d "$(jq -nc --arg m "$MODEL" '{model: $m, stream: false}')"; then
        log "pull failed; will retry next boot (or run: hc pull $MODEL)"
        exit 0
    fi
fi

mkdir -p /var/lib/hailcore
touch /var/lib/hailcore/firstboot.done
log "ready"
