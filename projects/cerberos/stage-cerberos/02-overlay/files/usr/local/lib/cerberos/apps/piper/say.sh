#!/bin/bash
# Text to speech with Piper.  say.sh "text"   or   cerb ask "..." | say.sh
set -euo pipefail
VOICE=${PIPER_VOICE:-en_US-lessac-medium}
DIR=${XDG_DATA_HOME:-$HOME/.local/share}/piper
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main
mkdir -p "$DIR"
if [ ! -f "$DIR/$VOICE.onnx" ]; then
    IFS=_- read -r lang region name quality <<<"$VOICE"
    path="${lang}/${lang}_${region}/${name}/${quality}/${VOICE}"
    echo "Fetching voice $VOICE..." >&2
    curl -fL -o "$DIR/$VOICE.onnx" "$BASE/$path.onnx"
    curl -fL -o "$DIR/$VOICE.onnx.json" "$BASE/$path.onnx.json"
fi
if [ $# -gt 0 ]; then text="$*"; elif [ ! -t 0 ]; then text=$(cat); else
    read -r -p "say » " text
fi
out=$(mktemp --suffix=.wav)
trap 'rm -f "$out"' EXIT
printf '%s\n' "$text" | piper --model "$DIR/$VOICE.onnx" --output_file "$out"
aplay -q "$out"
