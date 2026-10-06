#!/bin/bash
# Speech to text.  listen.sh            live from the microphone
#                  listen.sh file.wav   transcribe a file
W=/opt/whisper.cpp
MODEL=$W/models/ggml-base.en.bin
if [ $# -gt 0 ]; then
    exec "$W/build/bin/whisper-cli" -m "$MODEL" -t 4 -f "$@"
fi
echo "Listening. Speak; Ctrl-C to stop. Pipe a file in instead with: listen.sh file.wav"
exec "$W/build/bin/whisper-stream" -m "$MODEL" -t 4 --step 3000 --length 8000
