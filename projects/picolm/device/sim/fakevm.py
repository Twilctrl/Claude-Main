"""A headless stand-in for Picoware's ViewManager: a character-grid screen and scripted keys.

Only the calls PicoLM makes are provided: draw.text/clear/swap/font_size/size,
input_manager.button/reset, alert() and back(). The screen is kept as a grid
of character cells so a run can be printed at the end.
"""

from picoware.system.buttons import BUTTON_NONE
from picoware.system.vector import Vector


class FakeDraw:
    def __init__(self, cell_w=7, cell_h=12, width=320, height=320):
        self.font_size = Vector(cell_w, cell_h)
        self.size = Vector(width, height)
        self.cells = {}
        self.swaps = 0

    def text(self, position, text, color=None, font_size=-1):
        cx, cy = position.x // self.font_size.x, position.y // self.font_size.y
        for i in range(len(text)):
            self.cells[(cx + i, cy)] = text[i]

    def clear(self, position=None, size=None, color=None):
        if position is None:
            self.cells = {}
            return
        x0, y0 = position.x // self.font_size.x, position.y // self.font_size.y
        x1 = (position.x + size.x) // self.font_size.x
        y1 = (position.y + size.y + self.font_size.y - 1) // self.font_size.y
        for key in list(self.cells):
            if x0 <= key[0] < x1 and y0 <= key[1] < y1:
                del self.cells[key]

    def fill_screen(self, color=None):
        self.cells = {}

    def swap(self):
        self.swaps += 1

    def render(self):
        cols = self.size.x // self.font_size.x
        rows = self.size.y // self.font_size.y
        lines = []
        for y in range(rows):
            lines.append("".join(self.cells.get((x, y), " ") for x in range(cols)).rstrip())
        return "\n".join(lines)


class FakeInput:
    def __init__(self, script):
        self.script = script  # {run() call index: button}
        self.tick = 0
        self.pending = BUTTON_NONE

    @property
    def button(self):
        return self.pending

    def reset(self):
        self.pending = BUTTON_NONE

    def advance(self):
        self.pending = self.script.get(self.tick, BUTTON_NONE)
        self.tick += 1


class FakeViewManager:
    def __init__(self, script=None):
        self.draw = FakeDraw()
        self.input_manager = FakeInput(script or {})
        self.active = True
        self.alerts = []
        self.has_psram = True

    def alert(self, message, back=False):
        self.alerts.append(message)
        print("[alert]", message)
        return True

    def back(self):
        self.active = False
