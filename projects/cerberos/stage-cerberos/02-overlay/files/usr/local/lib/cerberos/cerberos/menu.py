"""The CerberOS start menu: a Kali-style categorized launcher in the terminal.

Left: numbered categories. Right: that category's apps and models.
Enter launches (installing first if needed); everything else is on screen.
"""

import curses
import textwrap

from . import apps as A
from .config import HEAD_LABEL, mem_total_gb

KEYS = "↑↓ move  ←→ switch  1-9 jump  ⏎ launch  i install  x remove  s status  g gate  d doctor  q quit"
KEYS_ASCII = "arrows move  1-9 jump  Enter launch  i install  x remove  s status  g gate  d doctor  q quit"


class Item:
    def __init__(self, kind, obj, label, ready, note=""):
        self.kind, self.obj, self.label, self.ready, self.note = kind, obj, label, ready, note


class State:
    def __init__(self, ctx, models_only):
        self.ctx = ctx
        self.models_only = models_only
        self.cat = 0
        self.row = 0
        self.focus = 0  # 0 categories, 1 items
        self.message = ""
        self.refresh()

    def refresh(self):
        ctx = self.ctx
        self.heads = ctx.client.head_states()
        self.gate = ctx.client.alive()
        self.installed = ctx.client.installed_names() if self.gate else set()
        self.ram = mem_total_gb()
        self.cats = []
        for c in ctx.catalog.categories:
            items = []
            if not self.models_only:
                for a in ctx.catalog.apps_in(c.id):
                    running = A.is_running(a)
                    items.append(Item("app", a, a.name, A.is_installed(a),
                                      "running" if running else ""))
            for m in ctx.catalog.models_in(c.id):
                note = m.head
                if m.head == "cpu" and m.ram_gb and self.ram and m.ram_gb > self.ram:
                    note = f"needs {m.ram_gb:g}GB"
                elif m.head == "remote" and not ctx.conf["CERBEROS_REMOTE_URL"]:
                    note = "remote"
                if m.origin:
                    note = f"{m.origin}] [{note}"
                items.append(Item("model", m, m.name, m.alias in self.installed, note))
            if items:
                self.cats.append((c, items))
        self.cat = min(self.cat, len(self.cats) - 1)

    @property
    def items(self):
        return self.cats[self.cat][1] if self.cats else []

    @property
    def current(self):
        items = self.items
        return items[min(self.row, len(items) - 1)] if items else None


def _colors():
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    ash = 8 if curses.COLORS >= 16 else curses.COLOR_WHITE
    curses.init_pair(1, curses.COLOR_RED, bg)        # frame, blood
    curses.init_pair(2, curses.COLOR_YELLOW, bg)     # rust, tallow when bold
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_RED)  # selection: bone on blood
    curses.init_pair(4, ash, bg)                     # dim text
    curses.init_pair(5, curses.COLOR_GREEN, bg)      # ok
    curses.init_pair(6, curses.COLOR_MAGENTA, bg)    # crimson accent
    curses.init_pair(7, curses.COLOR_WHITE, bg)


def _put(win, y, x, text, attr=0, width=None):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    text = text[: (width if width is not None else w - x - 1)]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _box(win, y, x, h, w, title, active):
    attr = curses.color_pair(1) | (curses.A_BOLD if active else 0)
    _put(win, y, x, "┌" + "─" * (w - 2) + "┐", attr)
    for i in range(1, h - 1):
        _put(win, y + i, x, "│", attr)
        _put(win, y + i, x + w - 1, "│", attr)
    _put(win, y + h - 1, x, "└" + "─" * (w - 2) + "┘", attr)
    _put(win, y, x + 2, f" {title} ", curses.color_pair(2) | curses.A_BOLD)


def draw(scr, st, unicode_ok):
    scr.erase()
    H, W = scr.getmaxyx()
    if H < 18 or W < 64:
        _put(scr, 0, 0, "Make the terminal at least 64x18 for the menu (or use: cerb help)")
        return
    fire = curses.color_pair(2) | curses.A_BOLD
    blood = curses.color_pair(1) | curses.A_BOLD
    dim = curses.color_pair(4)
    ok = curses.color_pair(5) | curses.A_BOLD

    # header: title + heads
    _put(scr, 0, 1, "▓▒░ " if unicode_ok else "## ", blood)
    _put(scr, 0, 5, "C E R B E R O S", fire)
    _put(scr, 0, 21, " ░▒▓" if unicode_ok else " ##", blood)
    _put(scr, 0, 26, "three heads · one gate · no cloud", dim)
    x = 1
    for head in ("npu", "cpu", "remote"):
        s = st.heads.get(head)
        attr = dim if s is None else (ok if s else curses.color_pair(1) | curses.A_BOLD)
        mark = "■" if s else ("·" if s is None else "x")
        _put(scr, 1, x, f"{mark} {HEAD_LABEL[head]}", attr)
        x += len(HEAD_LABEL[head]) + 5
    _put(scr, 1, x, f"{'■' if st.gate else 'x'} GATE :{st.ctx.conf['CERBEROS_GATE_PORT']}",
         ok if st.gate else blood)

    # panes
    top, foot = 3, 6
    ph = H - top - foot
    cw = max(24, min(34, W // 3))
    _box(scr, top, 0, ph, cw, "Categories", st.focus == 0)
    for i, (c, items) in enumerate(st.cats):
        if i >= ph - 2:
            break
        label = f" {c.label} "
        attr = curses.color_pair(3) | curses.A_BOLD if i == st.cat else (
            fire if st.focus == 0 and i == st.cat else curses.color_pair(7))
        _put(scr, top + 1 + i, 1, label.ljust(cw - 2), attr, cw - 2)

    iw = W - cw - 1  # curses can't write the bottom-right cell
    cat, items = st.cats[st.cat] if st.cats else (None, [])
    _box(scr, top, cw, ph, iw, cat.tagline if cat else "", st.focus == 1)
    visible = ph - 2
    st.row = max(0, min(st.row, len(items) - 1))
    start = max(0, st.row - visible + 1)
    for i, it in enumerate(items[start:start + visible]):
        idx = start + i
        y = top + 1 + i
        sel = st.focus == 1 and idx == st.row
        icon = ("► " if it.kind == "app" else "♦ ") if unicode_ok else ("> " if it.kind == "app" else "* ")
        tag = ("ready" if it.ready else "get") if it.kind == "app" else ("have" if it.ready else "pull")
        tag = f"[{it.note}] [{tag}]" if it.note else f"[{tag}]"
        name_w = iw - 4 - len(tag) - 1
        line = (icon + it.label)[:name_w].ljust(name_w)
        if sel:
            _put(scr, y, cw + 1, (" " + line + " " + tag).ljust(iw - 2), curses.color_pair(3) | curses.A_BOLD)
        else:
            _put(scr, y, cw + 2, line, fire if it.kind == "model" else curses.color_pair(7))
            _put(scr, y, cw + 2 + name_w + 1, tag, ok if it.ready else dim)

    # footer: description of the selected thing
    cur = st.current if st.focus == 1 else None
    fy = top + ph
    if cur is not None:
        if cur.kind == "model":
            m = cur.obj
            maker = f"{m.maker} ({m.origin})  ·  " if m.maker else ""
            head = f"{m.alias}  ·  {maker}{HEAD_LABEL.get(m.head, m.head)}  ·  {', '.join(m.strengths)}"
            desc = m.blurb
        else:
            a = cur.obj
            head = f"{a.id}  ·  {a.kind}" + (f"  ·  port {a.port}" if a.port else "")
            desc = a.blurb
        _put(scr, fy, 1, head, curses.color_pair(6) | curses.A_BOLD)
        for i, line in enumerate(textwrap.wrap(desc, W - 3)[:3]):
            _put(scr, fy + 1 + i, 1, line, curses.color_pair(7))
    elif cat is not None:
        _put(scr, fy, 1, f"{cat.label} — {cat.tagline}", curses.color_pair(6) | curses.A_BOLD)
        _put(scr, fy + 1, 1, f"{len(items)} entries. Press → or Enter to open.", dim)
    if st.message:
        _put(scr, H - 2, 1, st.message, fire)
    _put(scr, H - 1, 0, (KEYS if unicode_ok else KEYS_ASCII)[: W - 1], dim)


def ui(scr, st, unicode_ok):
    curses.curs_set(0)
    _colors()
    scr.keypad(True)
    while True:
        draw(scr, st, unicode_ok)
        scr.refresh()
        k = scr.getch()
        st.message = ""
        n = len(st.items)
        if k == ord("q"):
            return None
        if k == curses.KEY_RESIZE:
            continue
        if ord("1") <= k <= ord("9") and k - ord("1") < len(st.cats):
            st.cat, st.row, st.focus = k - ord("1"), 0, 1
        elif k in (curses.KEY_UP, ord("k")):
            if st.focus == 0:
                st.cat, st.row = max(0, st.cat - 1), 0
            else:
                st.row = max(0, st.row - 1)
        elif k in (curses.KEY_DOWN, ord("j")):
            if st.focus == 0:
                st.cat, st.row = min(len(st.cats) - 1, st.cat + 1), 0
            else:
                st.row = min(n - 1, st.row + 1)
        elif k in (curses.KEY_RIGHT, ord("l"), 9):
            st.focus = 1
        elif k in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB):
            st.focus = 0
        elif k in (10, 13, curses.KEY_ENTER):
            if st.focus == 0:
                st.focus = 1
            elif st.current:
                it = st.current
                return ("launch", it.obj.id) if it.kind == "app" else ("chat", it.obj.alias)
        elif k == ord("i") and st.focus == 1 and st.current:
            it = st.current
            return ("install", it.obj.id) if it.kind == "app" else ("pull", it.obj.alias)
        elif k == ord("x") and st.focus == 1 and st.current:
            it = st.current
            return ("uninstall", it.obj.id) if it.kind == "app" else ("rm", it.obj.alias)
        elif k == ord("s"):
            return ("status",)
        elif k == ord("g"):
            return ("gate",)
        elif k == ord("d"):
            return ("doctor",)
        elif k == ord("r"):
            st.refresh()
            st.message = "refreshed"


def run_menu(ctx, models_only=False, cli_main=None):
    """Loop: show the menu, run the chosen action as a child `cerb` process
    (so apps that exec or re-run under sudo don't take the menu down)."""
    import locale
    import os
    import subprocess
    import sys
    locale.setlocale(locale.LC_ALL, "")
    unicode_ok = "UTF-8" in (locale.nl_langinfo(locale.CODESET) or "").upper()
    st = State(ctx, models_only)
    if not st.cats:
        print("The catalog is empty: check /etc/cerberos/catalog.toml")
        return 1
    os.environ.setdefault("ESCDELAY", "25")
    while True:
        action = curses.wrapper(ui, st, unicode_ok)
        if action is None:
            return 0
        rc = subprocess.call([sys.executable, "-m", "cerberos", *action])
        if action[0] not in ("chat",):
            try:
                input("\n  press Enter to return to the menu")
            except (EOFError, KeyboardInterrupt):
                pass
        st.refresh()
        st.message = f"{' '.join(action)}: {'done' if not rc else f'exit {rc}'}"
