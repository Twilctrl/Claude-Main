#!/bin/bash -e
# Stage the hailo-ollama .deb (fetched by build.sh) into the image, if we have one.

if [ -f files/hailo-genai.deb ]; then
	install -m 644 files/hailo-genai.deb "${ROOTFS_DIR}/tmp/hailo-genai.deb"
fi
