#!/bin/bash -e

# Big, crisp Terminus console font: readable on a 1080p HDMI panel, and it has
# the box-drawing glyphs the banner and menu use.
sed -i \
	-e 's/^CODESET=.*/CODESET="Uni2"/' \
	-e 's/^FONTFACE=.*/FONTFACE="Terminus"/' \
	-e 's/^FONTSIZE=.*/FONTSIZE="16x32"/' \
	/etc/default/console-setup

# Pre-login screen: the bleeding banner, plus where to reach this box.
{
	printf '\033[2J\033[H'
	i=0
	while IFS= read -r line; do
		case $i in 0) c="1;97" ;; 1) c="37" ;; 2) c="1;91" ;; 3) c="91" ;; [4-7]) c="31" ;; *) c="35" ;; esac
		printf '\033[%sm%s\033[0m\n' "$c" "$line"
		i=$((i + 1))
	done < /usr/local/lib/cerberos/banner.txt
	printf '\033[90m  // three heads · one gate · no cloud\033[0m\n'
	printf '\033[31m  // \\n · \\l · ip \\4 · ssh in and type \033[1;93mcerb\033[0m\n\n'
} > /etc/issue

# Brand the OS (fastfetch, hostnamectl, login screens) while keeping Debian's
# ID fields so apt and scripts still recognise the base.
sed -i --follow-symlinks "s/^PRETTY_NAME=.*/PRETTY_NAME=\"CerberOS (Raspberry Pi OS base)\"/" /etc/os-release
