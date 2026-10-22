"""Scripted walks through the PicoLM UI with checks. MicroPython- and CPython-compatible.

Used by tests/test_ui.py (CPython) and run directly on a 32-bit MicroPython:

    mkdir -p /tmp/picolm_ui && cp models/picolm-s-int8.* /tmp/picolm_ui/
    micropython -X heapsize=24M device/sim/ui_scenario.py /tmp/picolm_ui/picolm-s-int8.plm

The model must sit in a writable folder: the UI saves settings.json next to it.
"""

import gc
import sys

_here = __file__.replace("\\", "/").rsplit("/", 1)[0] if "/" in __file__.replace("\\", "/") else "."
for _p in (_here, _here + "/.."):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import shim  # noqa: E402

shim.install()

from fakevm import FakeViewManager  # noqa: E402
from picoware.system.buttons import (BUTTON_BACK, BUTTON_CENTER, BUTTON_DOWN, BUTTON_ESCAPE,  # noqa: E402
                                     BUTTON_LEFT, BUTTON_RIGHT, BUTTON_UP)
from picolm_lib import hal  # noqa: E402
from picolm_lib import ui as ui_mod  # noqa: E402


class Driver:
    """Drives device/main.py the way Picoware's view manager does: one key per run() call."""

    def __init__(self, model_path, cell_h=12, record=True):
        hal.default_model_path = lambda: model_path
        import main
        self.main = main
        self.vm = FakeViewManager()
        self.vm.draw.font_size.y = cell_h
        assert main.start(self.vm), self.vm.alerts
        self.ui = main._app
        self.tokens = []
        if not record:
            return
        put = self.ui._put_token
        tokens = self.tokens

        def record(tok):
            tokens.append(tok)
            put(tok)

        self.ui._put_token = record

    def press(self, button):
        inp = self.vm.input_manager
        inp.pending = button
        self.main.run(self.vm)
        inp.reset()

    def type(self, text):
        self.vm.keyboard.type(text)
        self.main.run(self.vm)

    def idle(self, until, limit=100000):
        for _ in range(limit):
            if until():
                return True
            self.main.run(self.vm)
        return until()

    def finish(self):
        return self.idle(lambda: self.ui.phase >= ui_mod.STOPPED)

    def menu_items(self):
        return list(self.ui.menu.items)

    def close(self):
        self.main.stop(self.vm)


def check_wrap(ui, text):
    """Every line fits, and the words on screen are exactly the words generated."""
    shown = list(ui.lines) + [bytes(ui.cur[:ui.cur_n]).decode()]
    for line in shown:
        assert len(line) <= ui.cols, line
    assert " ".join(shown).split() == text.split(), (" ".join(shown)[:200], text[:200])
    return shown


def scenario_settings(model):
    d = Driver(model)
    ui = d.ui
    assert ui.view == ui_mod.MENU and d.menu_items()[0] == "New story..."
    d.press(BUTTON_DOWN)
    d.press(BUTTON_DOWN)
    assert ui.menu.selected_index == ui_mod.M_TEMP
    d.press(BUTTON_RIGHT)
    assert ui.settings["temp10"] == 9 and d.menu_items()[ui_mod.M_TEMP] == "Temperature: 0.9"
    d.press(BUTTON_LEFT)
    d.press(BUTTON_LEFT)
    assert ui.settings["temp10"] == 7
    d.press(BUTTON_CENTER)                     # CENTER on a value steps it up
    assert ui.settings["temp10"] == 8 and ui.menu.selected_index == ui_mod.M_TEMP
    d.press(BUTTON_DOWN)
    d.press(BUTTON_RIGHT)
    assert ui.settings["top_k"] == 40
    d.press(BUTTON_LEFT)
    assert ui.settings["top_k"] == 20
    for _ in range(3):
        d.press(BUTTON_DOWN)
    assert ui.menu.selected_index == ui_mod.M_OVERLAY
    d.press(BUTTON_CENTER)
    assert ui.settings["overlay"] == 0 and d.menu_items()[ui_mod.M_OVERLAY] == "Debug overlay: off"
    d.press(BUTTON_CENTER)
    d.press(BUTTON_DOWN)
    d.press(BUTTON_CENTER)                     # Exit
    assert not d.vm.active
    d.close()
    print("settings: ok")


def scenario_golden_story(model):
    d = Driver(model)
    ui = d.ui
    d.press(BUTTON_DOWN)
    d.press(BUTTON_CENTER)                     # Again: the default prompt, seed 1234 -> golden case
    assert ui.view == ui_mod.STORY and ui.check == "golden ..."
    gc.collect()
    assert d.finish()
    assert ui.phase == ui_mod.DONE and ui.check == "golden OK", ui.check
    assert d.vm.draw.partial == 0
    text = ui.settings["prompt"] + "".join(ui.decode[t] for t in d.tokens)
    check_wrap(ui, text)
    assert ui.settings["seed"] == ui.seed + 1
    print("golden story: ok, %d tokens, %d lines" % (len(d.tokens), len(ui.lines) + 1))
    print(d.vm.draw.render())
    d.press(BUTTON_BACK)
    assert ui.view == ui_mod.MENU and ui.menu.selected_index == ui_mod.M_AGAIN
    d.close()


def scenario_prompt_scroll_stop(model):
    d = Driver(model, cell_h=48)               # huge font: 4 text rows, so the story has to scroll
    ui = d.ui
    seed0 = ui.settings["seed"]
    d.press(BUTTON_CENTER)                     # New story...
    assert ui.view == ui_mod.PROMPT and d.vm.keyboard.response == "Once upon a time"
    d.press(BUTTON_BACK)                       # cancel: back to the menu, prompt unchanged
    assert ui.view == ui_mod.MENU and ui.settings["prompt"] == "Once upon a time"
    d.press(BUTTON_CENTER)
    d.type("The little dog")
    assert ui.view == ui_mod.STORY and ui.settings["prompt"] == "The little dog"
    assert d.finish()
    text = "The little dog" + "".join(ui.decode[t] for t in d.tokens)
    check_wrap(ui, text)
    shown = (list(ui.lines) + [bytes(ui.cur[:ui.cur_n]).decode()])[-(ui.row + 1):]
    on_screen = [d.vm.draw.row_text(ui.top + r * ui.ch) for r in range(ui.row + 1)]
    assert on_screen == [s.rstrip() for s in shown], (on_screen, shown)
    assert len(ui.lines) + 1 > ui.rows, "story too short to scroll"
    live_first = len(ui.lines) - ui.row
    d.press(BUTTON_UP)
    assert ui.view_top == max(0, live_first - ui.rows // 2), (ui.view_top, live_first)
    first = ui.view_top
    assert [d.vm.draw.row_text(ui.top + r * ui.ch) for r in range(ui.rows)] == \
        [s.rstrip() for s in ui.lines[first:first + ui.rows]]
    for _ in range(len(ui.lines)):
        d.press(BUTTON_DOWN)
    assert ui.view_top == -1
    d.press(BUTTON_CENTER)                     # next story with the next seed
    assert ui.phase == ui_mod.PRIMING and ui.seed == seed0 + 1
    d.idle(lambda: ui.phase == ui_mod.GENERATING)
    d.main.run(d.vm)
    d.press(BUTTON_CENTER)                     # stop it
    assert ui.phase == ui_mod.STOPPED
    d.press(BUTTON_ESCAPE)                     # Esc also leaves the story
    assert ui.view == ui_mod.MENU
    d.press(BUTTON_BACK)
    assert not d.vm.active and d.vm.draw.partial == 0
    d.close()
    print("prompt, scroll, stop: ok")


def scenario_settings_persist(model):
    d = Driver(model)
    assert d.ui.settings["prompt"] == "The little dog" and d.ui.settings["seed"] == 1237
    d.close()
    print("settings persisted: ok")


def scenario_allocation(model):
    """Heap allocated per generated token by engine + UI (MicroPython only).

    The fake screen is put in quiet mode and tokens are not recorded, so only
    the app's own allocations are counted (Picoware's draw.text allocates nothing).
    """
    if not hasattr(gc, "mem_alloc"):
        return None
    d = Driver(model, record=False)
    d.vm.draw.quiet = True
    ui = d.ui
    ui.settings["length"] = 400
    d.press(BUTTON_DOWN)
    d.press(BUTTON_CENTER)
    d.idle(lambda: ui.phase == ui_mod.GENERATING)
    gc.collect()
    gc.disable()
    a0, n0 = gc.mem_alloc(), ui.n
    d.idle(lambda: ui.n - n0 >= 200 or ui.phase >= ui_mod.STOPPED)
    per = (gc.mem_alloc() - a0) / max(1, ui.n - n0)
    gc.enable()
    d.close()
    print("heap allocated per token incl. UI: %.1f B (%d tokens)" % (per, ui.n - n0))
    return per


def run_all(model):
    scenario_settings(model)
    scenario_golden_story(model)
    scenario_prompt_scroll_stop(model)
    scenario_settings_persist(model)
    return scenario_allocation(model)


if __name__ == "__main__":
    run_all(sys.argv[1])
    print("all UI scenarios passed")
