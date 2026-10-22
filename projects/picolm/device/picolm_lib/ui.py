"""PicoLM user interface for Picoware: home menu, prompt entry, streaming story view.

Kept apart from the inference engine (engine.py). Talks to Picoware only through
the view manager: draw, input_manager, keyboard, the theme colours, back() and
the Menu component (APIs listed in docs/FLASHING.md).

Views:
  MENU   Picoware Menu. UP/DOWN move, LEFT/RIGHT change a setting, CENTER
         selects, BACK/ESC exits the app.
  PROMPT Picoware's keyboard component edits the prompt (its own keys: Enter
         saves, the exit key cancels).
  STORY  The story streams in with word wrap, scrolling half a page when the
         screen fills. CENTER stops a running story or starts the next one,
         UP/DOWN scroll back once it has stopped, BACK/ESC return to the menu.

Picoware calls run() over and over and it must not block, so reading the prompt
and generating both happen in time slices of SLICE_US, and the screen is
swapped at most every SWAP_MS (a full-screen swap costs ~30 ms).
"""

import gc
import json

from . import hal

SLICE_US = 100000
SWAP_MS = 150
STATUS_MS = 500
HISTORY = 120               # lines kept for scrolling back
MAX_PROMPT = 160            # characters
TOPKS = (0, 1, 2, 3, 5, 10, 20, 40)
LENGTHS = (50, 100, 200, 400, 800, 1600)
DEFAULTS = {"prompt": "Once upon a time", "temp10": 8, "top_k": 20, "length": 400, "seed": 1234,
            "overlay": 1}

MENU, PROMPT, STORY = 0, 1, 2
PRIMING, GENERATING, STOPPED, DONE = 0, 1, 2, 3
# Menu rows
M_NEW, M_AGAIN, M_TEMP, M_TOPK, M_LEN, M_SEED, M_OVERLAY, M_EXIT = range(8)


def temp_q8(temp10):
    """Temperature in tenths -> the engine's Q8 (0.8 -> 205); 0 means greedy."""
    return (temp10 * 256 + 5) // 10


def _cycle(values, current, step):
    i = values.index(current) if current in values else 0
    return values[(i + step) % len(values)]


class App:
    def __init__(self, vm):
        from picoware.system.buttons import (BUTTON_BACK, BUTTON_CENTER, BUTTON_DOWN, BUTTON_ESCAPE,
                                             BUTTON_LEFT, BUTTON_RIGHT, BUTTON_UP)
        from picoware.system.vector import Vector

        from .engine import Engine

        self.B_BACK, self.B_ESC, self.B_CENTER = BUTTON_BACK, BUTTON_ESCAPE, BUTTON_CENTER
        self.B_UP, self.B_DOWN, self.B_LEFT, self.B_RIGHT = BUTTON_UP, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT
        self.vm = vm
        self.draw = vm.draw
        self.fg, self.bg, self.sel = vm.foreground_color, vm.background_color, vm.selected_color
        self.vec = Vector(0, 0)
        self.vsize = Vector(0, 0)
        fs = self.draw.font_size
        self.cw, self.ch = fs.x, fs.y
        self.width, self.height = self.draw.size.x, self.draw.size.y
        self.cols = self.width // self.cw
        self.top = self.ch + 4
        self.rows = (self.height - self.top - self.ch - 4) // self.ch
        self.status_y = self.height - self.ch - 1
        self.chars = [chr(i) for i in range(128)]   # str per byte, so drawing never allocates
        self.cur = bytearray(self.cols)              # the line being written
        self.menu = None

        model = hal.default_model_path()
        self.settings_path = model[:model.rfind("/") + 1] + "settings.json"
        self.settings = self._load_settings()
        self._splash("loading model...")
        self.eng = Engine(model)
        self.decode = self.eng.tok.tokens
        self.show_menu(M_NEW)

    # -- settings -------------------------------------------------------------

    def _load_settings(self):
        s = dict(DEFAULTS)
        try:
            with open(self.settings_path) as f:
                saved = json.load(f)
            for k in s:
                if k in saved and type(saved[k]) is type(s[k]):
                    s[k] = saved[k]
        except (OSError, ValueError):
            pass
        return s

    def save_settings(self):
        try:
            with open(self.settings_path, "w") as f:
                json.dump(self.settings, f)
        except OSError:
            pass  # read-only or missing card: settings just are not remembered

    # -- small drawing helpers --------------------------------------------------

    def _text(self, x, y, s, color=None):
        self.vec.x, self.vec.y = x, y
        if color is None:
            self.draw.text(self.vec, s)
        else:
            self.draw.text(self.vec, s, color)

    def _fill(self, x, y, w, h):
        self.vec.x, self.vec.y = x, y
        self.vsize.x, self.vsize.y = w, h
        self.draw.clear(self.vec, self.vsize)

    def _splash(self, note):
        self.draw.fill_screen()
        self._text(4, self.height // 2 - self.ch, "PicoLM")
        self._text(4, self.height // 2 + 2, note)
        self.draw.swap()

    # -- menu ----------------------------------------------------------------

    def _menu_items(self):
        s = self.settings
        p = s["prompt"].replace("\n", " ")
        room = max(4, self.cols - 12)
        t = s["temp10"]
        return ["New story...",
                "Again: " + (p if len(p) <= room else p[:room - 1] + "~"),
                "Temperature: " + ("greedy" if t == 0 else "%d.%d" % (t // 10, t % 10)),
                "Top-k: " + (str(s["top_k"]) if s["top_k"] else "off"),
                "Length: %d" % s["length"],
                "Seed: %d" % s["seed"],
                "Debug overlay: " + ("on" if s["overlay"] else "off"),
                "Exit"]

    def show_menu(self, index):
        from picoware.gui.menu import Menu

        if self.menu is None:
            self.menu = Menu(self.draw, "PicoLM", 0, self.height, self.fg, self.bg, self.sel, self.fg, 2)
        else:
            self.menu.clear()
        for item in self._menu_items():
            self.menu.add_item(item)
        self.menu.set_selected(index)
        self.menu.draw()
        self.view = MENU
        gc.collect()

    def _adjust(self, row, step):
        s = self.settings
        if row == M_TEMP:
            s["temp10"] = min(20, max(0, s["temp10"] + step))
        elif row == M_TOPK:
            s["top_k"] = _cycle(TOPKS, s["top_k"], step)
        elif row == M_LEN:
            s["length"] = _cycle(LENGTHS, s["length"], step)
        elif row == M_SEED:
            s["seed"] = max(0, s["seed"] + step)
        elif row == M_OVERLAY:
            s["overlay"] = 1 - s["overlay"]
        else:
            return
        self.show_menu(row)

    def _run_menu(self, b):
        m = self.menu
        row = m.selected_index
        if b == self.B_UP:
            m.scroll_up()
        elif b == self.B_DOWN:
            m.scroll_down()
        elif b == self.B_LEFT:
            self._adjust(row, -1)
        elif b == self.B_RIGHT:
            self._adjust(row, 1)
        elif b == self.B_CENTER:
            if row == M_NEW:
                self.open_prompt()
            elif row == M_AGAIN:
                self.start_story()
            elif row == M_EXIT:
                self.vm.back()
            else:
                self._adjust(row, 1)
        elif b == self.B_BACK or b == self.B_ESC:
            self.vm.back()

    # -- prompt ----------------------------------------------------------------

    def open_prompt(self):
        kb = self.vm.keyboard
        kb.reset()
        kb.title = "Story prompt (Enter starts)"
        kb.response = self.settings["prompt"]
        self.view = PROMPT
        kb.run(force=True)

    def _run_prompt(self):
        kb = self.vm.keyboard
        if not kb.run(max_characters=MAX_PROMPT):   # cancelled with the exit key
            kb.reset()
            self.show_menu(M_NEW)
        elif kb.is_finished:
            text = kb.response
            kb.reset()
            if text.strip():
                self.settings["prompt"] = text
                self.start_story()
            else:
                self.show_menu(M_NEW)

    # -- story -----------------------------------------------------------------

    def start_story(self):
        s = self.settings
        eng = self.eng
        gc.collect()
        if eng.ensure_loaded():
            self._splash("model reloaded into PSRAM")
        self.seed = s["seed"]
        s["seed"] = self.seed + 1          # "Again" continues with a new seed
        self.save_settings()
        self.temp = temp_q8(s["temp10"])
        self.top_k = s["top_k"]
        self.length = s["length"]
        self.ids = [eng.eot] + eng.tok.encode(s["prompt"])
        self.pi = 0
        eng.reset()
        eng.seed(self.seed)
        self.check = ""
        self.golden = self._find_golden(s["prompt"])
        self.n = 0
        self.gen_us = 0
        self.phase = PRIMING
        self.lines = []
        self.cur_n = 0
        self.word_start = 0
        self.row = 0
        self.view_top = -1                  # -1: following the live text
        self.view = STORY
        self.draw.fill_screen()
        self._clear_text()
        for c in s["prompt"]:
            self._put_char(c)
        self._chrome()
        self.draw.swap()
        self.last_swap = self.last_status = hal.ticks_ms()

    def _find_golden(self, prompt):
        """Token ids from a golden case matching these settings (exactness check), else None."""
        model = hal.default_model_path()
        try:
            with open(hal.golden_path(model)) as f:
                for case in json.load(f)["cases"]:
                    if (case["prompt"], case["temp_q8"], case["top_k"], case["seed"]) == \
                            (prompt, self.temp, self.top_k, self.seed):
                        self.check = "golden ..."
                        return case["ids"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def _run_story(self, b):
        if b == self.B_BACK or b == self.B_ESC:
            self.show_menu(M_AGAIN)
            return
        if b == self.B_CENTER:
            if self.phase in (PRIMING, GENERATING):
                self.phase = STOPPED
                self._chrome()
                self.draw.swap()
            else:
                self.start_story()
            return
        if self.phase in (STOPPED, DONE) and (b == self.B_UP or b == self.B_DOWN):
            self._scroll(-(self.rows // 2) if b == self.B_UP else self.rows // 2)
            return
        if self.phase == PRIMING:
            self._prime()
        elif self.phase == GENERATING:
            self._generate()
        else:
            return
        now = hal.ticks_ms()
        ended = self.phase >= STOPPED
        if ended or hal.ticks_diff(now, self.last_swap) >= SWAP_MS:
            # The status line is rebuilt (a few small strings) at most every STATUS_MS,
            # so a whole story stays well under the heap threshold that would trigger GC.
            if ended or hal.ticks_diff(now, self.last_status) >= STATUS_MS:
                self._chrome()
                self.last_status = now
            self.draw.swap()
            self.last_swap = now

    def _prime(self):
        eng, ids = self.eng, self.ids
        last = len(ids) - 1
        t0 = hal.ticks_us()
        while self.pi < last and hal.ticks_diff(hal.ticks_us(), t0) < SLICE_US:
            eng.step(ids[self.pi], False)
            self.pi += 1
        if self.pi == last:
            eng.step(ids[last], True)
            self.pi += 1
            self.phase = GENERATING

    def _generate(self):
        eng = self.eng
        golden = self.golden
        t0 = hal.ticks_us()
        while hal.ticks_diff(hal.ticks_us(), t0) < SLICE_US:
            tok = eng.sample(self.temp, self.top_k)
            if golden is not None and self.n < len(golden):
                if tok != golden[self.n]:
                    self.check = "golden FAIL@%d" % self.n
                    golden = self.golden = None
                elif self.n == len(golden) - 1:
                    self.check = "golden OK"
            if tok == eng.eot or self.n >= self.length:
                self.phase = DONE
                break
            self._put_token(tok)
            eng.step(tok)
            self.n += 1
        self.gen_us += hal.ticks_diff(hal.ticks_us(), t0)

    # -- streaming text with word wrap -------------------------------------------

    def _put_token(self, tok):
        s = self.decode[tok]
        if len(s) == 1:  # the common case: no iteration, no new string objects
            self._put_char(s)
        else:
            for c in s:
                self._put_char(c)

    def _put_char(self, c):
        o = ord(c)
        if o == 10:
            self._newline(self.cur_n)
            return
        if o < 32 or o > 126:  # control tokens are not drawn
            return
        if self.cur_n >= self.cols:
            if o == 32:                       # a space at the margin just ends the line
                self._newline(self.cur_n)
                return
            if self.word_start > 0:           # move the unfinished word to the next line
                ws, n = self.word_start, self.cur_n
                word = bytes(self.cur[ws:n])
                if self.view_top < 0:
                    self._fill(ws * self.cw, self.top + self.row * self.ch, (n - ws) * self.cw, self.ch)
                self._newline(ws)
                self.cur[0:n - ws] = word
                self.cur_n = n - ws
                if self.view_top < 0:
                    self._text(0, self.top + self.row * self.ch, word.decode())
            else:                             # a word longer than a line: hard break
                self._newline(self.cur_n)
        self.cur[self.cur_n] = o
        if self.view_top < 0:
            self._text(self.cur_n * self.cw, self.top + self.row * self.ch, self.chars[o])
        self.cur_n += 1
        if o == 32:
            self.word_start = self.cur_n

    def _newline(self, keep):
        """Commit the first `keep` characters of the current line (minus trailing spaces)."""
        while keep > 0 and self.cur[keep - 1] == 32:
            keep -= 1
        self.lines.append(bytes(self.cur[:keep]).decode())
        if len(self.lines) > HISTORY:
            self.lines.pop(0)
        self.cur_n = 0
        self.word_start = 0
        self.row += 1
        if self.row >= self.rows:
            self.row = self.rows - self.rows // 2
            if self.view_top < 0:
                self._redraw(len(self.lines) - self.row, live=True)

    def _redraw(self, first, live):
        """Draw history lines from `first`; when live, the current line goes below them."""
        self._clear_text()
        lines = self.lines
        y = self.top
        last = min(len(lines), first + self.rows)
        for i in range(max(0, first), last):
            self._text(0, y, lines[i])
            y += self.ch
        if live and self.cur_n:
            self._text(0, y, bytes(self.cur[:self.cur_n]).decode())

    def _scroll(self, delta):
        live_first = len(self.lines) - self.row  # first line on screen when following the text
        first = (live_first if self.view_top < 0 else self.view_top) + delta
        first = max(0, min(live_first, first))
        if first == live_first:
            self.view_top = -1
            self._redraw(len(self.lines) - self.row, live=True)
        else:
            self.view_top = first
            self._redraw(first, live=False)
        self._chrome()
        self.draw.swap()

    def _clear_text(self):
        self._fill(0, self.top, self.width, self.rows * self.ch)

    def _chrome(self):
        """Title line and the status / debug line."""
        t = self.settings["temp10"]
        title = "PicoLM  T%s k%s seed %d  %d/%d" % (
            "greedy" if t == 0 else "%d.%d" % (t // 10, t % 10),
            self.top_k or "-", self.seed, self.n, self.length)
        self._fill(0, 0, self.width, self.top - 1)
        self._text(2, 2, title[:self.cols])
        overlay = self.settings["overlay"]
        if self.phase == PRIMING:
            info = "prompt %d/%d" % (self.pi, len(self.ids))
        elif self.phase == GENERATING:
            info = self.check if overlay else "CENTER stop  BACK menu"
        else:
            info = "stopped" if self.phase == STOPPED else "done"
            if not overlay:
                info += ": CENTER new  UP/DOWN scroll"
            elif self.check:
                info += " " + self.check
        if overlay:
            us = self.gen_us // self.n if self.n else 0
            tps = 1000000 / us if us else 0
            info = "%.1ft/s %dms %dK %s %s" % (tps, us // 1000, hal.mem_free() // 1024, self.eng.mode, info)
        self._fill(0, self.status_y, self.width, self.ch + 1)
        self._text(0, self.status_y, info[:self.cols], self.sel)

    # -- Picoware entry points ------------------------------------------------------

    def run(self):
        if self.view == PROMPT:
            self._run_prompt()
            return
        b = self.vm.input_manager.button
        if self.view == MENU:
            if b != -1:
                self._run_menu(b)
        else:
            self._run_story(b)

    def close(self):
        self.save_settings()
        if self.eng is not None:
            self.eng.close()
            self.eng = None
        self.menu = None
