"""PicoLM for Picoware, milestone 2: generate from a fixed prompt and stream it to the screen.

Installed as /sd/picoware/apps/PicoLM.py with the engine in picolm_lib/ next to
it (see docs/FLASHING.md); start it from Library > Applications > Custom.

Keys: BACK exits, CENTER starts another story with the next seed.

The first story uses the settings of golden case 1, so the status line can
confirm that the device reproduces the PC reference token for token.
Picoware calls run() repeatedly and it must not block, so each call
generates for at most SLICE_MS and the screen is redrawn at most every
SWAP_MS (a full-screen swap costs ~30 ms).
"""

PROMPT = "Once upon a time"
TEMP_Q8 = 205       # temperature 0.8 in Q8
TOP_K = 20
MAX_TOKENS = 600
FIRST_SEED = 1234
SLICE_MS = 100
SWAP_MS = 150

_app = None


class _App:
    def __init__(self, vm):
        import gc
        from picoware.system.vector import Vector
        from picolm_lib import hal
        from picolm_lib.engine import Engine

        self.vm = vm
        self.hal = hal
        self.draw = vm.draw
        self.vec = Vector(0, 0)
        fs = self.draw.font_size
        self.cw, self.ch = fs.x, fs.y
        size = self.draw.size
        self.width, self.height = size.x, size.y
        self.cols = self.width // self.cw
        self.top = self.ch + 4                       # title line
        self.rows = (self.height - self.top - self.ch - 4) // self.ch
        self.status_y = self.height - self.ch - 1
        self.text_size = Vector(self.width, self.rows * self.ch)
        self.status_size = Vector(self.width, self.ch + 1)

        self._title("loading model...")
        gc.collect()
        path = hal.default_model_path()
        self.eng = Engine(path)
        self.decode = self.eng.tok.tokens
        self.golden = self._golden(path)
        self.seed = FIRST_SEED
        self.new_story()

    def _golden(self, path):
        """Token ids from the golden case that matches this app's settings, if present."""
        try:
            import json
            from picolm_lib.selftest import golden_path
            with open(golden_path(path)) as f:
                for case in json.load(f)["cases"]:
                    if (case["prompt"], case["temp_q8"], case["top_k"], case["seed"]) == \
                            (PROMPT, TEMP_Q8, TOP_K, FIRST_SEED):
                        return case["ids"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    # -- drawing --------------------------------------------------------------

    def _text(self, x, y, s):
        self.vec.x, self.vec.y = x, y
        self.draw.text(self.vec, s)

    def _title(self, note):
        d = self.draw
        d.clear()
        self._text(2, 2, "PicoLM - " + note)
        d.swap()

    def _clear_text(self):
        self.vec.x, self.vec.y = 0, self.top
        self.draw.clear(self.vec, self.text_size)
        self.col = 0
        self.row = 0

    def _put_char(self, ch):
        """Character-wrapped streaming text; a full page starts again at the top."""
        if ch == "\n" or self.col >= self.cols:
            self.col = 0
            self.row += 1
        if self.row >= self.rows:
            self._clear_text()
        if ch != "\n":
            self._text(self.col * self.cw, self.top + self.row * self.ch, ch)
            self.col += 1

    def _put(self, s):
        if len(s) == 1:  # the common case: no iteration, no new string objects
            self._put_char(s)
        else:
            for ch in s:
                self._put_char(ch)

    def _status(self):
        d = self.draw
        self.vec.x, self.vec.y = 0, self.status_y
        d.clear(self.vec, self.status_size)
        us = self.gen_us // self.n if self.n else 0
        tps = 1000000 / us if us else 0
        line = "%.1f tok/s %d ms %dK %s" % (tps, us // 1000, self.hal.mem_free() // 1024, self.eng.mode)
        if self.golden is not None and self.seed == FIRST_SEED:
            line += " " + self.check
        self._text(0, self.status_y, line[:self.cols])

    # -- generation -------------------------------------------------------------

    def new_story(self):
        self._title("thinking (seed %d)..." % self.seed)
        self.eng.ensure_loaded()
        self.eng.seed(self.seed)
        self.eng.prime(PROMPT)
        self._title("seed %d, temp %.1f, top-k %d" % (self.seed, TEMP_Q8 / 256, TOP_K))
        self._clear_text()
        self._put(PROMPT)
        self.n = 0
        self.gen_us = 0
        self.done = False
        self.check = "golden ..."
        self.last_swap = self.hal.ticks_ms()
        self._status()
        self.draw.swap()

    def tick(self):
        hal = self.hal
        if self.done:
            return
        eng = self.eng
        t0 = hal.ticks_us()
        while hal.ticks_diff(hal.ticks_us(), t0) < SLICE_MS * 1000:
            tok = eng.sample(TEMP_Q8, TOP_K)
            if self.golden is not None and self.seed == FIRST_SEED and self.n < len(self.golden):
                if tok != self.golden[self.n]:
                    self.check = "golden FAIL@%d" % self.n
                elif self.n == len(self.golden) - 1 and self.check == "golden ...":
                    self.check = "golden OK"
            if tok == eng.eot or self.n >= MAX_TOKENS:
                self.done = True
                self._put("\n[end]")
                break
            self._put(self.decode[tok])
            eng.step(tok)
            self.n += 1
        self.gen_us += hal.ticks_diff(hal.ticks_us(), t0)
        now = hal.ticks_ms()
        if self.done or hal.ticks_diff(now, self.last_swap) >= SWAP_MS:
            self._status()
            self.draw.swap()
            self.last_swap = now

    def close(self):
        self.eng.close()
        self.eng = None


def start(view_manager):
    global _app
    try:
        _app = _App(view_manager)
    except Exception as e:  # show the reason instead of silently failing to launch
        view_manager.alert("PicoLM: %s" % e)
        return False
    return True


def run(view_manager):
    from picoware.system.buttons import BUTTON_BACK, BUTTON_CENTER

    inp = view_manager.input_manager
    button = inp.button
    if button == BUTTON_BACK:
        inp.reset()
        view_manager.back()
        return
    if button == BUTTON_CENTER and _app is not None:
        inp.reset()
        _app.seed += 1
        _app.new_story()
        return
    if _app is not None:
        try:
            _app.tick()
        except Exception as e:  # e.g. PSRAM trouble: report it and leave cleanly
            view_manager.alert("PicoLM stopped: %s" % e)
            view_manager.back()


def stop(view_manager):
    global _app
    from gc import collect

    if _app is not None:
        _app.close()
    _app = None
    collect()
