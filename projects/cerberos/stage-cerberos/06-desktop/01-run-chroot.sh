#!/bin/bash -e
# Desktop flavor: XFCE with the CerberOS hellfire theme and categorized start menu.

rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/cerberos/cerberos.svg \
	-o /usr/share/backgrounds/cerberos/cerberos.png
gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

cat > /etc/lightdm/lightdm-gtk-greeter.conf <<'GREETER'
[greeter]
background=/usr/share/backgrounds/cerberos/cerberos.png
user-background=false
theme-name=Adwaita-dark
icon-theme-name=Papirus-Dark
font-name=JetBrains Mono 11
default-user-image=/usr/share/icons/hicolor/scalable/apps/cerberos.svg
indicators=~host;~spacer;~clock;~spacer;~session;~power
clock-format=%H:%M
position=50%,center 70%,center
GREETER

# Whisker menu entries for the generated categories.
PYTHONPATH=/usr/local/lib/cerberos python3 -m cerberos sync

systemctl enable lightdm.service
systemctl set-default graphical.target
