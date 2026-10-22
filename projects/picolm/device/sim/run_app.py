"""Run the PicoLM app headless: show the menu, write one story with the saved prompt, print the screens.

From projects/picolm:
    python device/sim/run_app.py models/picolm-s-int8.plm
    micropython -X heapsize=24M device/sim/run_app.py models/picolm-s-int8.plm

The app saves settings.json next to the model, as it does on the SD card.
For the scripted checks see ui_scenario.py.
"""

import sys

_here = __file__.replace("\\", "/").rsplit("/", 1)[0] if "/" in __file__.replace("\\", "/") else "."
sys.path.insert(0, _here)

from ui_scenario import Driver  # noqa: E402  (also sets up paths and the CPython shim)

from picoware.system.buttons import BUTTON_CENTER, BUTTON_DOWN  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else "models/picolm-s-int8.plm"
d = Driver(model, record=False)
print(d.vm.draw.render())
print("-" * 45)
d.press(BUTTON_DOWN)      # "Again: <prompt>"
d.press(BUTTON_CENTER)
d.finish()
print(d.vm.draw.render())
d.close()
