"""PicoLM self-test as a Picoware app (installed as /sd/picoware/apps/PicoLM-Test.py).

Runs picolm_lib.selftest and shows its report on screen (and on USB serial):
whether the device reproduces the PC reference token for token, load time,
ms per token split into weight reads and compute, heap use, allocations per
token and raw PSRAM/SD read speeds. Takes about a minute.

Keys: UP/DOWN scroll, BACK exits.
"""

_tb = None
_text = ""
_started = False


def _log(line):
    global _text
    print(line)
    _text += line + "\n"
    if _tb is not None:
        _tb.set_text(_text)
        _tb.jump_to_bottom()


def start(view_manager):
    global _tb, _text, _started
    from picoware.gui.textbox import TextBox

    draw = view_manager.draw
    draw.fill_screen()
    _tb = TextBox(draw, 0, draw.size.y)
    _text = ""
    _started = False
    _log("PicoLM self-test (about a minute)...")
    return True


def run(view_manager):
    global _started
    from picoware.system.buttons import BUTTON_BACK, BUTTON_DOWN, BUTTON_UP

    inp = view_manager.input_manager
    button = inp.button
    if button == BUTTON_BACK:
        inp.reset()
        view_manager.back()
        return
    if button == BUTTON_UP:
        inp.reset()
        _tb.scroll_up()
    elif button == BUTTON_DOWN:
        inp.reset()
        _tb.scroll_down()
    if not _started:
        _started = True
        try:
            from picolm_lib import selftest
            selftest.run(log=_log)
            _log("done. UP/DOWN scroll, BACK exits")
        except Exception as e:  # report instead of crashing the launcher
            _log("error: %r" % e)


def stop(view_manager):
    global _tb, _text
    from gc import collect

    _tb = None
    _text = ""
    collect()
