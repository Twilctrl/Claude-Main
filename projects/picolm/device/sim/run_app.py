"""Run the PicoLM Picoware app headless and print the final screen.

From projects/picolm:
    python device/sim/run_app.py models/picolm-s-int8.plm [run_calls]
    micropython -X heapsize=24M device/sim/run_app.py models/picolm-s-int8.plm

Drives device/main.py through start() / run() / stop() with a fake view
manager, then presses BACK. Useful to check the app logic without hardware.
"""

import sys

_here = __file__.replace("\\", "/").rsplit("/", 1)[0] if "/" in __file__.replace("\\", "/") else "."
sys.path.insert(0, _here)
sys.path.insert(0, _here + "/..")

import shim  # noqa: E402

shim.install()

from fakevm import FakeViewManager  # noqa: E402
from picoware.system.buttons import BUTTON_BACK  # noqa: E402
from picolm_lib import hal  # noqa: E402

args = sys.argv[1:]
model = args[0] if args else "models/picolm-s-int8.plm"
calls = int(args[1]) if len(args) > 1 else 400
hal.default_model_path = lambda: model

import main as app  # noqa: E402

vm = FakeViewManager({calls: BUTTON_BACK})
if not app.start(vm):
    print("start() failed:", vm.alerts)
    sys.exit(1)
i = 0
while vm.active and i <= calls:
    vm.input_manager.advance()
    app.run(vm)
    i += 1
    if app._app is not None and app._app.done and vm.input_manager.tick < calls:
        vm.input_manager.tick = calls  # story finished: press BACK next
print(vm.draw.render())
print("-- run() calls: %d, swaps: %d, tokens: %d, status: %s" %
      (i, vm.draw.swaps, app._app.n, app._app.check))
app.stop(vm)
