#!/usr/bin/env bash
# Fetch the pinned upstream sources this project builds on into work/upstream/
# (git-ignored). Each source is checked out at the exact commit in versions.env.
# Re-running is safe: an existing checkout is verified, not re-cloned.
#
# Usage:
#   scripts/00_fetch_upstream.sh                  # compiler repo + its wiki
#   scripts/00_fetch_upstream.sh --with-hailo-src # also HailoRT + hailo-ollama sources
#
# The Hailo sources are only read, to check the runtime contract (MIT-licensed).
# Nothing here downloads the Dataflow Compiler or any Hailo binary.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(dirname "$here")"
# shellcheck source=SCRIPTDIR/../versions.env
source "$project/versions.env"
dest="$project/work/upstream"
mkdir -p "$dest"

# Anonymous git reads of public repos; LFS objects are never needed here.
export GIT_LFS_SKIP_SMUDGE=1

fetch() { # fetch <name> <url> <commit>
    local name=$1 url=$2 commit=$3
    local dir="$dest/$name"
    if [[ -d $dir/.git ]]; then
        local have
        have=$(git -C "$dir" rev-parse HEAD)
        if [[ $have == "$commit" ]]; then
            echo "ok    $name @ ${commit:0:12} (already present)"
            return
        fi
        echo "FAIL  $name is at ${have:0:12}, expected ${commit:0:12}; remove $dir and re-run" >&2
        return 1
    fi
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    # Fetching a bare commit works on GitHub; fall back to full history if not.
    if ! git -C "$dir" fetch -q --depth 1 origin "$commit" 2>/dev/null; then
        git -C "$dir" fetch -q origin
    fi
    git -C "$dir" checkout -q --detach "$commit"
    echo "ok    $name @ ${commit:0:12}"
}

fetch hailo-10h-llm-compiler "$UPSTREAM_REPO" "$UPSTREAM_COMMIT"
fetch hailo-10h-llm-compiler.wiki "$UPSTREAM_WIKI_REPO" "$UPSTREAM_WIKI_COMMIT"

if [[ ${1:-} == --with-hailo-src ]]; then
    fetch hailort "$HAILORT_SRC_REPO" "$HAILORT_SRC_COMMIT"
    fetch hailo_model_zoo_genai "$GENAI_ZOO_SRC_REPO" "$GENAI_ZOO_SRC_COMMIT"
fi

echo "upstream sources in $dest"
