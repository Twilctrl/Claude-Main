#!/bin/bash -e
# Let the first user run the docker web apps without sudo.
on_chroot <<CHROOT
usermod -aG docker,video ${FIRST_USER_NAME}
CHROOT
