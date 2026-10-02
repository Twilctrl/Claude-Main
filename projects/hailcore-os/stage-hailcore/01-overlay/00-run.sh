#!/bin/bash -e
# Copy the hailcore file tree into the image.

cp -a files/. "${ROOTFS_DIR}/"
chown -R root:root "${ROOTFS_DIR}/etc/hailcore" "${ROOTFS_DIR}/usr/local/lib/hailcore"
chmod 755 "${ROOTFS_DIR}"/usr/local/bin/* "${ROOTFS_DIR}"/usr/local/lib/hailcore/*.sh \
	"${ROOTFS_DIR}"/usr/local/lib/hailcore/*.py "${ROOTFS_DIR}"/etc/update-motd.d/*
