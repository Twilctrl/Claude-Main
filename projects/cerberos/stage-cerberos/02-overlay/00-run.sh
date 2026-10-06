#!/bin/bash -e
# Copy the CerberOS file tree into the image.

cp -a files/. "${ROOTFS_DIR}/"
chown -R root:root "${ROOTFS_DIR}/etc/cerberos" "${ROOTFS_DIR}/usr/local/lib/cerberos"
chmod 755 "${ROOTFS_DIR}/usr/local/bin/cerb" "${ROOTFS_DIR}"/usr/local/lib/cerberos/*.sh \
	"${ROOTFS_DIR}"/usr/local/lib/cerberos/apps/*/*.sh "${ROOTFS_DIR}/etc/update-motd.d/10-cerberos"
