#!/usr/bin/env bash
# Build the CerberOS image with pi-gen (the tool that builds Raspberry Pi OS).
#
#   ./build.sh                                # console edition, built in Docker
#   CERBEROS_FLAVOR=desktop ./build.sh        # XFCE desktop edition
#   NO_DOCKER=1 sudo ./build.sh               # build natively on Debian/Pi OS
#
# Settings come from the environment; see "Settings" below or README.md.
# The finished image lands in ./deploy/.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)

# --- Settings -----------------------------------------------------------------
PI_GEN_REPO=${PI_GEN_REPO:-https://github.com/RPi-Distro/pi-gen.git}
PI_GEN_REF=${PI_GEN_REF:-arm64}                     # the 64-bit branch of pi-gen
RELEASE=${RELEASE:-trixie}
CERBEROS_FLAVOR=${CERBEROS_FLAVOR:-lite}            # lite (console) or desktop (XFCE)
CERBEROS_HOSTNAME=${CERBEROS_HOSTNAME:-cerberos}
CERBEROS_USER=${CERBEROS_USER:-hellhound}
CERBEROS_PASS=${CERBEROS_PASS:-}                    # empty: generate a random one
CERBEROS_SSH_PUBKEY=${CERBEROS_SSH_PUBKEY:-}        # path to a .pub file, optional
CERBEROS_TIMEZONE=${CERBEROS_TIMEZONE:-Etc/UTC}
CERBEROS_KEYMAP=${CERBEROS_KEYMAP:-us}
CERBEROS_WIFI_COUNTRY=${CERBEROS_WIFI_COUNTRY:-}
# Bake the catalog's preload="build" CPU models into the image (needs Docker).
CERBEROS_BAKE_MODELS=${CERBEROS_BAKE_MODELS:-1}
# The Hailo GenAI Model Zoo .deb that provides hailo-ollama (path or URL, or "none").
HAILO_GENAI_DEB=${HAILO_GENAI_DEB:-https://dev-public.hailo.ai/2025_12/Hailo10/hailo_gen_ai_model_zoo_5.1.1_arm64.deb}
# Ollama for the CPU head (path or URL, or "none").
OLLAMA_TARBALL=${OLLAMA_TARBALL:-https://ollama.com/download/ollama-linux-arm64.tar.zst}
WORK=${WORK:-$HERE/work}
# ------------------------------------------------------------------------------

say() { printf '\033[1;31m[cerberos]\033[0m %s\n' "$*"; }
die() { printf '\033[1;91m[cerberos]\033[0m %s\n' "$*" >&2; exit 1; }

case $CERBEROS_FLAVOR in lite|desktop) ;; *) die "CERBEROS_FLAVOR must be lite or desktop" ;; esac
[[ $CERBEROS_PASS != *"'"* ]] || die "CERBEROS_PASS can't contain a single quote"

PI_GEN=$WORK/pi-gen
DL=$WORK/downloads
mkdir -p "$WORK" "$DL" "$HERE/deploy"

# fetch <path-or-url> <dest>: copy a local file or download (cached) a URL.
fetch() {
    local src=$1 dest=$2
    case $src in
        http://*|https://*)
            local cache
            cache="$DL/$(basename "${src%%\?*}")"
            if [[ ! -s $cache ]]; then
                curl -fL --retry 3 -o "$cache.part" "$src" && mv "$cache.part" "$cache" || {
                    rm -f "$cache.part"; return 1; }
            fi
            cp "$cache" "$dest" ;;
        *)
            [[ -f $src ]] || die "not found: $src"
            cp "$src" "$dest" ;;
    esac
}

if [[ ! -d $PI_GEN/.git ]]; then
    say "Cloning pi-gen ($PI_GEN_REF)"
    git clone --depth 1 --branch "$PI_GEN_REF" "$PI_GEN_REPO" "$PI_GEN"
else
    say "Updating pi-gen ($PI_GEN_REF)"
    git -C "$PI_GEN" fetch --depth 1 origin "$PI_GEN_REF"
    git -C "$PI_GEN" checkout -q FETCH_HEAD
fi

say "Installing the CerberOS stage ($CERBEROS_FLAVOR)"
STAGE=$PI_GEN/stage-cerberos
rm -rf "$STAGE"
cp -a "$HERE/stage-cerberos" "$STAGE"
[[ $CERBEROS_FLAVOR == desktop ]] || rm -rf "$STAGE/06-desktop"
# Only export our image, not the plain Lite image stage2 would also produce.
touch "$PI_GEN/stage2/SKIP_IMAGES"

# --- NPU head: hailo-ollama ---------------------------------------------------
if [[ $HAILO_GENAI_DEB == none ]]; then
    say "Skipping hailo-ollama (HAILO_GENAI_DEB=none)"
elif ! fetch "$HAILO_GENAI_DEB" "$STAGE/00-hailo/files/hailo-genai.deb"; then
    say "WARNING: couldn't fetch hailo-ollama; the NPU head will be missing."
    say "         Install it on the Pi later (see README), then: cerb restart npu"
fi

# --- CPU head: Ollama ---------------------------------------------------------
if [[ $OLLAMA_TARBALL == none ]]; then
    say "Skipping Ollama (OLLAMA_TARBALL=none)"
elif ! fetch "$OLLAMA_TARBALL" "$STAGE/01-ollama/files/ollama-linux-arm64.tar.zst"; then
    say "WARNING: couldn't fetch Ollama; the CPU head will be missing."
fi

# --- Bake models --------------------------------------------------------------
# Ollama's model files are architecture-independent, so an x86 Ollama in
# Docker can download them here and they run on the Pi as-is.
bake_list() {
    python3 - "$HERE/stage-cerberos/02-overlay/files/etc/cerberos/catalog.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    cat = tomllib.load(f)
print(" ".join(m["model"] for m in cat.get("model", [])
               if m.get("head") == "cpu" and m.get("preload") == "build"))
PY
}

if [[ $CERBEROS_BAKE_MODELS == 1 ]]; then
    if ! command -v docker >/dev/null; then
        say "WARNING: no Docker, so no baked models; the Pi downloads them at first boot."
    elif ! MODELS=$(bake_list 2>/dev/null); then
        say "WARNING: reading the catalog needs Python 3.11+; skipping baked models."
    elif [[ -n $MODELS ]]; then
        say "Baking models into the image: $MODELS"
        BAKE=$WORK/ollama-bake
        mkdir -p "$BAKE"
        docker rm -f cerberos-bake >/dev/null 2>&1 || true
        docker run -d --rm --name cerberos-bake -v "$BAKE:/root/.ollama" ollama/ollama >/dev/null
        for _ in $(seq 30); do
            docker exec cerberos-bake ollama list >/dev/null 2>&1 && break
            sleep 1
        done
        for m in $MODELS; do
            docker exec cerberos-bake ollama pull "$m" || say "WARNING: couldn't bake $m"
        done
        docker stop cerberos-bake >/dev/null
        mkdir -p "$STAGE/01-ollama/files/models"
        cp -a "$BAKE/models/." "$STAGE/01-ollama/files/models/"
    fi
fi

# --- pi-gen config ------------------------------------------------------------
if [[ -z $CERBEROS_PASS ]]; then
    CERBEROS_PASS=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 16 || true)
    GENERATED_PASS=1
fi
PUBKEY=""
if [[ -n $CERBEROS_SSH_PUBKEY ]]; then
    [[ -f $CERBEROS_SSH_PUBKEY ]] || die "CERBEROS_SSH_PUBKEY not found: $CERBEROS_SSH_PUBKEY"
    PUBKEY=$(cat "$CERBEROS_SSH_PUBKEY")
fi

say "Writing pi-gen config"
cat > "$PI_GEN/config" <<EOF
IMG_NAME='cerberos-$CERBEROS_FLAVOR'
PI_GEN_RELEASE='CerberOS'
RELEASE='$RELEASE'
DEPLOY_COMPRESSION='xz'
STAGE_LIST='stage0 stage1 stage2 stage-cerberos'
TARGET_HOSTNAME='$CERBEROS_HOSTNAME'
FIRST_USER_NAME='$CERBEROS_USER'
FIRST_USER_PASS='$CERBEROS_PASS'
DISABLE_FIRST_BOOT_USER_RENAME=1
ENABLE_SSH=1
PUBKEY_SSH_FIRST_USER='$PUBKEY'
LOCALE_DEFAULT='en_US.UTF-8'
KEYBOARD_KEYMAP='$CERBEROS_KEYMAP'
KEYBOARD_LAYOUT='English (US)'
TIMEZONE_DEFAULT='$CERBEROS_TIMEZONE'
WPA_COUNTRY='$CERBEROS_WIFI_COUNTRY'
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

cp -v "$PI_GEN"/deploy/*cerberos* "$HERE/deploy/"
say "Done. Image(s) in $HERE/deploy/"
say "Log in as '$CERBEROS_USER' at $CERBEROS_HOSTNAME.local, then type: cerb"
if [[ ${GENERATED_PASS:-0} == 1 ]]; then
    printf '%s\n' "$CERBEROS_PASS" > "$HERE/deploy/password.txt"
    chmod 600 "$HERE/deploy/password.txt"
    say "Generated password: $CERBEROS_PASS  (also saved to deploy/password.txt)"
fi
