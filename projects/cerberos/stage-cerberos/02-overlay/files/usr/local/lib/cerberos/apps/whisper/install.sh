#!/bin/bash
# Build whisper.cpp (speech to text) for the Pi 5's CPU and fetch the base.en model.
set -euo pipefail
DEST=/opt/whisper.cpp
apt-get install -y git build-essential cmake libsdl2-dev alsa-utils
if [ ! -d "$DEST/.git" ]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$DEST"
fi
cd "$DEST"
cmake -B build -DWHISPER_SDL2=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)" --config Release
sh ./models/download-ggml-model.sh base.en
