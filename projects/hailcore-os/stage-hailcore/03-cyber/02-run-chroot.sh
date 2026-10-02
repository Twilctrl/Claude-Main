#!/bin/bash -e

# Big, crisp Terminus console font: readable on a 1080p HDMI panel, and it has
# the box-drawing glyphs the banner uses.
sed -i \
	-e 's/^CODESET=.*/CODESET="Uni2"/' \
	-e 's/^FONTFACE=.*/FONTFACE="Terminus"/' \
	-e 's/^FONTSIZE=.*/FONTSIZE="16x32"/' \
	/etc/default/console-setup

# Pre-login screen: the banner in neon, plus where to reach this box.
{
	printf '\033[2J\033[H'
	i=0
	while IFS= read -r line; do
		case $i in 0|1) c=96 ;; 2|3) c=36 ;; *) c=95 ;; esac
		printf '\033[1;%sm%s\033[0m\n' "$c" "$line"
		i=$((i + 1))
	done < /usr/local/lib/hailcore/banner.txt
	printf '\033[2;35m  // neural edge node :: \\n :: \\l :: ip \\4\033[0m\n\n'
} > /etc/issue
