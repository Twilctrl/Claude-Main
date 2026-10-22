"""A headless stand-in for Picoware's ViewManager: a character-grid screen and scripted keys.

Only the calls PicoLM makes are provided: draw.text/clear/fill_screen/swap/font_size/size,
input_manager.button/reset, keyboard, the theme colours, alert() and back(). The
screen keeps each glyph at its pixel position so a run can be checked and printed.
"""

from picoware.system.buttons import BUTTON_BACK, BUTTON_NONE
from picoware.system.vector import Vector


class FakeDraw:
    """Glyphs kept at their pixel positions, like the LCD's framebuffer.

    clear() erases the glyphs inside the rectangle; a glyph it only partly covers
    is counted in .partial, because on the LCD that would leave half a character.
    """

    def __init__(self, cell_w=7, cell_h=12, width=320, height=320):
        self.font_size = Vector(cell_w, cell_h)
        self.size = Vector(width, height)
        self.glyphs = {}
        self.partial = 0
        self.swaps = 0
        self.quiet = False  # True: draw nothing, so allocation counts only see the app

    def text(self, position, text, color=None, font_size=-1):
        if self.quiet:
            return
        cw = self.font_size.x
        for i in range(len(text)):
            self.glyphs[(position.x + i * cw, position.y)] = text[i]

    def clear(self, position=None, size=None, color=None):
        if position is None:
            self.glyphs = {}
            return
        x0, y0 = position.x, position.y
        x1, y1 = x0 + size.x, y0 + size.y
        cw, ch = self.font_size.x, self.font_size.y
        for gx, gy in list(self.glyphs):
            if gx + cw <= x0 or gx >= x1 or gy + ch <= y0 or gy >= y1:
                continue
            if not (x0 <= gx and gx + cw <= x1 and y0 <= gy and gy + ch <= y1):
                self.partial += 1
            del self.glyphs[(gx, gy)]

    def fill_screen(self, color=None):
        self.glyphs = {}

    def swap(self):
        self.swaps += 1

    def row_text(self, y):
        """The characters drawn with their top edge at pixel row y."""
        cols = {}
        for (gx, gy), c in self.glyphs.items():
            if gy == y:
                cols[gx // self.font_size.x] = c
        return "".join(cols.get(i, " ") for i in range(max(cols) + 1)).rstrip() if cols else ""

    def render(self):
        ys = sorted(set(gy for _, gy in self.glyphs))
        return "\n".join(self.row_text(y) for y in ys)


class FakeInput:
    """Scripted input: {run() call index: button code, or ("type", text) for the keyboard}."""

    def __init__(self, script, vm):
        self.script = script
        self.vm = vm
        self.tick = 0
        self.pending = BUTTON_NONE

    @property
    def button(self):
        return self.pending

    def reset(self):
        self.pending = BUTTON_NONE

    def advance(self):
        step = self.script.get(self.tick, BUTTON_NONE)
        self.tick += 1
        if isinstance(step, tuple):  # ("type", text): typed into the keyboard, then Enter
            self.pending = BUTTON_NONE
            self.vm.keyboard.type(step[1])
        else:
            self.pending = step


class FakeKeyboard:
    """Picoware's Keyboard as seen by an app: run() is False once the exit key was pressed."""

    def __init__(self, inp):
        self.inp = inp
        self.reset()

    def reset(self):
        self.response = ""
        self.title = "Enter Text"
        self.is_finished = False
        self.just_stopped = False

    def run(self, swap=True, force=False, max_characters=0):
        if self.just_stopped:
            return False
        if self.inp.button == BUTTON_BACK:
            self.just_stopped = True
            self.inp.reset()
            return False
        return True

    def type(self, text):
        self.response = text
        self.is_finished = True


class FakeViewManager:
    def __init__(self, script=None):
        self.draw = FakeDraw()
        self.input_manager = FakeInput(script or {}, self)
        self.keyboard = FakeKeyboard(self.input_manager)
        self.foreground_color = 0xFFFF
        self.background_color = 0x0000
        self.selected_color = 0x001F
        self.active = True
        self.alerts = []
        self.has_psram = True

    def alert(self, message, back=False):
        self.alerts.append(message)
        print("[alert]", message)
        return True

    def back(self):
        self.active = False
