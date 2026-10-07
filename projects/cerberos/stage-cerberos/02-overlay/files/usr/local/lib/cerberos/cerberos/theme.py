"""Terminal colours. With the CerberOS console palette loaded, the basic ANSI
colours become: 31 dark red, 91 red, 33 amber, 93 sand, 35 crimson, 36 steel,
90 grey, 37/97 off-white. (The helper names below predate the wording.)"""

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

# Banner gradient: off-white at the top fading to red, darker toward the bottom.
BANNER_COLOURS = ("1;97", "37", "1;91", "91", "31", "31", "31", "31", "35", "35", "35")
TAGLINE = "local AI for Raspberry Pi 5 + Hailo-10H"


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
    """Print a heading with a short scramble-in effect."""
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
