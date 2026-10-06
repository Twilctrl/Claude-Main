#!/bin/bash -e
# Wire the CerberOS shell theme and dotfiles into the first user's account and /etc/skel.

HOOK='[ -f /etc/cerberos/bashrc.sh ] && . /etc/cerberos/bashrc.sh'

for rc in "${ROOTFS_DIR}/etc/skel/.bashrc" "${ROOTFS_DIR}/home/${FIRST_USER_NAME}/.bashrc"; do
	if [ -f "$rc" ] && ! grep -qF "$HOOK" "$rc"; then
		printf '\n# CerberOS theme\n%s\n' "$HOOK" >> "$rc"
	fi
done

on_chroot <<CHROOT
install -d -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /home/${FIRST_USER_NAME}/.config/fastfetch
install -m 644 -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /etc/skel/.tmux.conf /home/${FIRST_USER_NAME}/.tmux.conf
install -m 644 -o ${FIRST_USER_NAME} -g ${FIRST_USER_NAME} /etc/skel/.config/fastfetch/config.jsonc /home/${FIRST_USER_NAME}/.config/fastfetch/config.jsonc
ln -sfn /srv/local-models /home/${FIRST_USER_NAME}/local-models
ln -sfn /srv/local-models /etc/skel/local-models
CHROOT
