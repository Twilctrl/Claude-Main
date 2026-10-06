"""Colours and flourishes. With the CerberOS console palette loaded, the basic
ANSI colours become: 31 arterial red, 91 fresh blood, 33 rust, 93 tallow,
35 clotted crimson, 36 cold iron, 90 ash, 37/97 bone."""

import os
import random
import sys
import time

from .config import LIB

TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
FX = TTY and os.environ.get("CERBEROS_NO_FX") != "1"


def _c(code):
    return (lambda s: f"\033[{code}m{s}\033[0m") if TTY else (lambda s: str(s))


blood, ember, fire, crimson, glitch, ash, bone = (
    _c(c) for c in ("1;31", "33", "1;93", "35", "36", "90", "1;97"))
red, ok_green, dim, bold = _c("91"), _c("92"), _c("2"), _c("1")

# Bone at the top, bleeding into red, clotting at the bottom of the drips.
BANNER_COLOURS = ("1;97", "37", "1;91", "91", "31", "31", "31", "31", "35", "35", "35")
TAGLINE = "three heads · one gate · no cloud"


def banner(out=sys.stdout):
    try:
        with open(os.path.join(LIB, "banner.txt")) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = ["CERBEROS"]
    for i, line in enumerate(lines):
        code = BANNER_COLOURS[min(i, len(BANNER_COLOURS) - 1)]
        out.write(f"\033[{code}m{line}\033[0m\n" if TTY else line + "\n")
    out.write(ash(f"  // {TAGLINE}") + "\n")


def decode(text, duration=0.45):
    """Print text as if it were rotting into place."""
    if not FX:
        print(bone(text))
        return
    glyphs = "▓▒░#%&*:."
    steps = 12
    for i in range(steps + 1):
        fixed = int(len(text) * i / steps)
        noise = "".join(c if c == " " else random.choice(glyphs) for c in text[fixed:])
        sys.stdout.write("\r" + bone(text[:fixed]) + blood(noise))
        sys.stdout.flush()
        time.sleep(duration / steps)
    sys.stdout.write("\n")


def dot(state):
    """Status bullet: True up, False down, None not configured."""
    if state is None:
        return ash("·")
    return ok_green("■") if state else red("■")


def kv(key, value, width=10):
    print(f"  {crimson(key.ljust(width))} {value}")


def ok(msg):
    print(f"  {ok_green('[ OK ]')} {msg}")


def warn(msg):
    print(f"  {ember('[WARN]')} {msg}")


def fail(msg):
    print(f"  {red('[FAIL]')} {msg}")
