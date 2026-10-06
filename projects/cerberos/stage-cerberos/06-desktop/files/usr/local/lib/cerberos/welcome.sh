#!/bin/sh
# First graphical login: open the CerberOS start menu in a terminal, once.
MARK=${XDG_CONFIG_HOME:-$HOME/.config}/cerberos/welcomed
[ -e "$MARK" ] && exit 0
mkdir -p "$(dirname "$MARK")" && touch "$MARK"
exec xfce4-terminal --maximize --title "CerberOS" --command "cerb"
