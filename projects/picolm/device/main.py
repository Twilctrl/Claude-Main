"""PicoLM for Picoware: a tiny language model that writes stories on the PicoCalc.

Installed as /sd/picoware/apps/PicoLM.py, with the engine and UI in
picolm_lib/ next to it (see docs/FLASHING.md). Start it from
Library > Applications > Custom > PicoLM. The interface lives in
picolm_lib/ui.py, the inference engine in picolm_lib/engine.py.
"""

_app = None


def start(view_manager):
    global _app
    try:
        from picolm_lib.ui import App
        _app = App(view_manager)
    except Exception as e:  # show the reason instead of silently failing to launch
        view_manager.alert("PicoLM: %s" % e)
        return False
    return True


def run(view_manager):
    if _app is None:
        return
    try:
        _app.run()
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
