#!/usr/bin/env bash
# Build the hailcore OS image with pi-gen (the tool that builds Raspberry Pi OS).
#
#   ./build.sh                 # build in Docker (recommended; works on x86 hosts)
#   NO_DOCKER=1 sudo ./build.sh   # build natively on a Debian/Pi OS host
#
# Settings come from the environment; see "Settings" below or README.md.
# The finished image lands in ./deploy/.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)

# --- Settings -----------------------------------------------------------------
PI_GEN_REPO=${PI_GEN_REPO:-https://github.com/RPi-Distro/pi-gen.git}
PI_GEN_REF=${PI_GEN_REF:-arm64}              # the 64-bit branch of pi-gen
RELEASE=${RELEASE:-trixie}
HAILCORE_HOSTNAME=${HAILCORE_HOSTNAME:-hailcore}
HAILCORE_USER=${HAILCORE_USER:-netrunner}
HAILCORE_PASS=${HAILCORE_PASS:-}             # empty: generate a random one
HAILCORE_SSH_PUBKEY=${HAILCORE_SSH_PUBKEY:-} # path to a .pub file, optional
HAILCORE_TIMEZONE=${HAILCORE_TIMEZONE:-Etc/UTC}
HAILCORE_KEYMAP=${HAILCORE_KEYMAP:-us}
HAILCORE_WIFI_COUNTRY=${HAILCORE_WIFI_COUNTRY:-}
# The Hailo GenAI Model Zoo .deb that provides hailo-ollama. A local path or URL.
# Set it to "none" to build without it (install it on the Pi later).
HAILO_GENAI_DEB=${HAILO_GENAI_DEB:-https://dev-public.hailo.ai/2025_12/Hailo10/hailo_gen_ai_model_zoo_5.1.1_arm64.deb}
WORK=${WORK:-$HERE/work}
# ------------------------------------------------------------------------------

say() { printf '\033[1;36m[hailcore]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[hailcore]\033[0m %s\n' "$*" >&2; exit 1; }

PI_GEN=$WORK/pi-gen
mkdir -p "$WORK" "$HERE/deploy"

if [[ ! -d $PI_GEN/.git ]]; then
    say "Cloning pi-gen ($PI_GEN_REF)"
    git clone --depth 1 --branch "$PI_GEN_REF" "$PI_GEN_REPO" "$PI_GEN"
else
    say "Updating pi-gen ($PI_GEN_REF)"
    git -C "$PI_GEN" fetch --depth 1 origin "$PI_GEN_REF"
    git -C "$PI_GEN" checkout -q FETCH_HEAD
fi

say "Installing the hailcore stage"
rm -rf "$PI_GEN/stage-hailcore"
cp -a "$HERE/stage-hailcore" "$PI_GEN/stage-hailcore"
# Only export our image, not the plain Lite image stage2 would also produce.
touch "$PI_GEN/stage2/SKIP_IMAGES"

DEB_DEST=$PI_GEN/stage-hailcore/00-hailo/files/hailo-genai.deb
case $HAILO_GENAI_DEB in
    none|"")
        say "Skipping hailo-ollama (HAILO_GENAI_DEB=none)" ;;
    http://*|https://*)
        say "Downloading the Hailo GenAI Model Zoo (hailo-ollama)"
        if ! curl -fL --retry 3 -o "$DEB_DEST" "$HAILO_GENAI_DEB"; then
            rm -f "$DEB_DEST"
            say "WARNING: download failed; building without hailo-ollama."
            say "         Install it on the Pi later, then run: hc doctor"
        fi ;;
    *)
        [[ -f $HAILO_GENAI_DEB ]] || die "HAILO_GENAI_DEB not found: $HAILO_GENAI_DEB"
        cp "$HAILO_GENAI_DEB" "$DEB_DEST" ;;
esac

if [[ -z $HAILCORE_PASS ]]; then
    HAILCORE_PASS=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 16 || true)
    GENERATED_PASS=1
fi

[[ $HAILCORE_PASS != *"'"* ]] || die "HAILCORE_PASS can't contain a single quote"

PUBKEY=""
if [[ -n $HAILCORE_SSH_PUBKEY ]]; then
    [[ -f $HAILCORE_SSH_PUBKEY ]] || die "HAILCORE_SSH_PUBKEY not found: $HAILCORE_SSH_PUBKEY"
    PUBKEY=$(cat "$HAILCORE_SSH_PUBKEY")
fi

say "Writing pi-gen config"
cat > "$PI_GEN/config" <<EOF
IMG_NAME='hailcore'
RELEASE='$RELEASE'
DEPLOY_COMPRESSION='xz'
STAGE_LIST='stage0 stage1 stage2 stage-hailcore'
TARGET_HOSTNAME='$HAILCORE_HOSTNAME'
FIRST_USER_NAME='$HAILCORE_USER'
FIRST_USER_PASS='$HAILCORE_PASS'
DISABLE_FIRST_BOOT_USER_RENAME=1
ENABLE_SSH=1
PUBKEY_SSH_FIRST_USER='$PUBKEY'
LOCALE_DEFAULT='en_US.UTF-8'
KEYBOARD_KEYMAP='$HAILCORE_KEYMAP'
KEYBOARD_LAYOUT='English (US)'
TIMEZONE_DEFAULT='$HAILCORE_TIMEZONE'
WPA_COUNTRY='$HAILCORE_WIFI_COUNTRY'
EOF

cd "$PI_GEN"
if [[ ${NO_DOCKER:-0} == 1 ]]; then
    [[ $EUID -eq 0 ]] || die "Native builds need root: NO_DOCKER=1 sudo $0"
    say "Building natively (this takes a while)"
    ./build.sh
else
    command -v docker >/dev/null || die "Docker not found. Install it, or use NO_DOCKER=1."
    say "Building in Docker (this takes a while)"
    # CONTINUE=1 reuses a previous build container, so a re-run resumes.
    CONTINUE=1 ./build-docker.sh
fi

cp -v "$PI_GEN"/deploy/*hailcore* "$HERE/deploy/"
say "Done. Image(s) in $HERE/deploy/"
say "Log in as '$HAILCORE_USER' at $HAILCORE_HOSTNAME.local"
if [[ ${GENERATED_PASS:-0} == 1 ]]; then
    printf '%s\n' "$HAILCORE_PASS" > "$HERE/deploy/password.txt"
    chmod 600 "$HERE/deploy/password.txt"
    say "Generated password: $HAILCORE_PASS  (also saved to deploy/password.txt)"
fi
