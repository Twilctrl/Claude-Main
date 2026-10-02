#!/bin/bash -e
# Wire the hailcore shell theme into the first user's account and /etc/skel.

HOOK='[ -f /etc/hailcore/bashrc.sh ] && . /etc/hailcore/bashrc.sh'

for rc in "${ROOTFS_DIR}/etc/skel/.bashrc" "${ROOTFS_DIR}/home/${FIRST_USER_NAME}/.bashrc"; do
	if [ -f "$rc" ] && ! grep -qF "$HOOK" "$rc"; then
		printf '\n# hailcore theme\n%s\n' "$HOOK" >> "$rc"
	fi
done

on_chroot <<CHROOT
install -d -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /home/${FIRST_USER_NAME}/.config/fastfetch
install -m 644 -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /etc/skel/.tmux.conf /home/${FIRST_USER_NAME}/.tmux.conf
install -m 644 -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /etc/skel/.config/fastfetch/config.jsonc /home/${FIRST_USER_NAME}/.config/fastfetch/config.jsonc
CHROOT
