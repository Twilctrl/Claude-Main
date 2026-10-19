#!/usr/bin/env bash
# Read-only readiness check for the compile host. Installs nothing and changes
# nothing; it reports what Phases 1-5 need and whether this machine has it.
# It never contacts the Raspberry Pi.
#
# Usage: scripts/00_check_env.sh [--wheel-dir DIR]
#   --wheel-dir  where you put the Dataflow Compiler wheel (default: work/dfc)
#
# Exit status: 0 when nothing blocks Phase 1, 1 otherwise.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(dirname "$here")"
# shellcheck source=SCRIPTDIR/../versions.env
source "$project/versions.env"

wheel_dir="$project/work/dfc"
if [[ ${1:-} == --wheel-dir ]]; then
    wheel_dir=${2:?--wheel-dir needs a directory}
fi

blockers=0
report() { # report <ok|warn|FAIL> <item> <detail>
    printf '%-5s %-15s %s\n' "$1" "$2" "$3"
    if [[ $1 == FAIL ]]; then
        blockers=$((blockers + 1))
    fi
}

arch=$(uname -m)
if [[ $arch == x86_64 ]]; then
    report ok arch "$arch"
else
    report FAIL arch "$arch (the Dataflow Compiler wheel is x86_64 only)"
fi

mem_gb=$(awk '/MemTotal/ {printf "%d", $2 / 1024 / 1024}' /proc/meminfo)
if ((mem_gb >= 64)); then
    report ok ram "${mem_gb} GB"
elif ((mem_gb >= 32)); then
    report warn ram "${mem_gb} GB: enough for Phase 1; quantizing Gemma at a 512 context may need 64 GB"
else
    report warn ram "${mem_gb} GB: TinyStories only; upstream caps the DFC container at 24 GB on 32 GB hosts"
fi

report ok cpus "$(nproc)"

mkdir -p "$project/work"
free_gb=$(df -Pk "$project/work" | awk 'NR == 2 {printf "%d", $4 / 1024 / 1024}')
if ((free_gb >= 100)); then
    report ok disk "${free_gb} GB free under work/"
else
    report warn disk "${free_gb} GB free under work/ (want 100+: the DFC image is ~15 GB, Gemma HARs run to tens of GB)"
fi

if ! command -v docker >/dev/null; then
    report FAIL docker "not installed"
elif ! docker info >/dev/null 2>&1; then
    report FAIL docker "client installed, daemon not reachable"
else
    report ok docker "server $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
fi

if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    report ok gpu "$(nvidia-smi -L | head -n 1)"
    if docker info 2>/dev/null | grep -qi nvidia; then
        report ok gpu-runtime "nvidia runtime registered with docker"
    else
        report warn gpu-runtime "nvidia-container-toolkit not registered with docker (GPU invisible in containers)"
    fi
elif [[ -e /dev/kfd ]]; then
    report ok gpu "AMD ROCm device (/dev/kfd); use upstream's Dockerfile.amd"
else
    report warn gpu "none: quantization runs on CPU (slow but works)"
fi

wheel="$wheel_dir/$DFC_WHEEL"
if [[ -f $wheel ]]; then
    report ok dfc-wheel "$wheel (sha256 $(sha256sum "$wheel" | cut -c1-16)...)"
else
    report FAIL dfc-wheel "not found at $wheel (download it from the Hailo Developer Zone)"
fi

upstream="$project/work/upstream/hailo-10h-llm-compiler"
if [[ -d $upstream/.git && $(git -C "$upstream" rev-parse HEAD) == "$UPSTREAM_COMMIT" ]]; then
    report ok upstream "hailo-10h-llm-compiler @ ${UPSTREAM_COMMIT:0:12}"
else
    report warn upstream "not fetched yet: run scripts/00_fetch_upstream.sh"
fi

# The token value is never printed.
token=${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}
if [[ -z $token && -f $HOME/.cache/huggingface/token ]]; then
    token=$(<"$HOME/.cache/huggingface/token")
fi
if [[ -z $token ]]; then
    report warn hf-token "not set (needed from Phase 2: $GEMMA_MODEL is gated)"
else
    code=$(curl -sL -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" \
        "https://huggingface.co/$GEMMA_MODEL/resolve/$GEMMA_REVISION/config.json")
    case $code in
        200) report ok gemma-access "token works and the Gemma terms are accepted" ;;
        401 | 403) report warn gemma-access "HTTP $code: accept the terms at https://huggingface.co/$GEMMA_MODEL" ;;
        *) report warn gemma-access "HTTP $code (network problem?)" ;;
    esac
fi

if ((blockers > 0)); then
    echo "$blockers blocker(s) for Phase 1"
    exit 1
fi
echo "nothing blocks Phase 1"
