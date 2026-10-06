#!/bin/bash -e
# Apps, the gate's service user, and the generated model tree and start menu.

if ! id cerberos >/dev/null 2>&1; then
	useradd --system --no-create-home --shell /usr/sbin/nologin cerberos
fi
mkdir -p /var/lib/cerberos

# Preinstalled Python CLIs (best effort: a PyPI hiccup shouldn't sink the build;
# `cerb install <app>` finishes the job on the Pi).
export PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin PIPX_MAN_DIR=/usr/local/share/man
for app in ${CERBEROS_PIPX_APPS:-llm}; do
	case $app in
		llm) pipx install llm && pipx inject llm llm-ollama || echo "WARNING: llm install failed" ;;
		*) pipx install "$app" || echo "WARNING: $app install failed" ;;
	esac
done

PYTHONPATH=/usr/local/lib/cerberos python3 -m cerberos sync

systemctl enable docker.service ollama.service cerberos-gate.service
