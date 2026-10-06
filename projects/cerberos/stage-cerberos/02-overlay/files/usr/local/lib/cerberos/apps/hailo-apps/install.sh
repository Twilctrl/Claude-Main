#!/bin/bash
# Install Hailo's vision demos (detection, pose, segmentation, depth) for the Pi camera.
set -euo pipefail
DEST=/opt/hailo-apps
apt-get install -y git python3-venv python3-dev
if [ ! -d "$DEST/.git" ]; then
    git clone --depth 1 https://github.com/hailo-ai/hailo-apps.git "$DEST"
fi
cd "$DEST"
# Hailo's installer sets up a venv with the right HailoRT bindings for this board.
./install.sh
cat > "$DEST/run.sh" <<'RUN'
#!/bin/bash
# CerberOS launcher for the Hailo vision demos.
cd /opt/hailo-apps
# shellcheck disable=SC1091
source setup_env.sh
echo "Hailo vision demos. Pick one (Ctrl-C to stop it):"
select demo in detection pose segmentation depth quit; do
    case $demo in
        detection) hailo-detect --input rpi ;;
        pose) hailo-pose --input rpi ;;
        segmentation) hailo-seg --input rpi ;;
        depth) hailo-depth --input rpi ;;
        *) exit 0 ;;
    esac
done
RUN
chmod +x "$DEST/run.sh"
chown -R "${SUDO_USER:-root}:" "$DEST"
