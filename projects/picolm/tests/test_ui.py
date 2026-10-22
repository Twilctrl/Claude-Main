"""The PicoLM Picoware UI driven through scripted key presses (CPython, simulated Picoware).

    python tests/test_ui.py        (or: python -m pytest tests)

Covers the menu and settings, prompt entry and cancel, the golden story with
word wrap checked against the generated text, scrolling, stop / next story,
Esc and BACK, and settings saved across launches. The same scenarios run on a
32-bit MicroPython via device/sim/ui_scenario.py.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "device", "sim"))
sys.path.insert(0, os.path.join(ROOT, "device"))

import ui_scenario  # noqa: E402


def test_ui_scenarios():
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("picolm-s-int8.plm", "picolm-s-int8.golden.json"):
            shutil.copy(os.path.join(ROOT, "models", name), tmp)
        ui_scenario.run_all(os.path.join(tmp, "picolm-s-int8.plm"))


if __name__ == "__main__":
    test_ui_scenarios()
    print("ok    test_ui_scenarios")
