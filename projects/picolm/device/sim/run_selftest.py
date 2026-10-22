"""Run the device self-test on a PC (CPython or a 32-bit MicroPython unix build).

From projects/picolm:
    python device/sim/run_selftest.py models/picolm-s-int8.plm [psram|ram|sd] [max_cases]
    micropython -X heapsize=24M device/sim/run_selftest.py models/picolm-s-int8.plm psram

"psram" uses the simulated 8 MB PSRAM in device/sim/picoware. CPython runs the
viper kernels as plain Python (slow: use max_cases 1); a 32-bit MicroPython
runs the real viper code.
"""

import sys

_here = __file__.replace("\\", "/").rsplit("/", 1)[0] if "/" in __file__.replace("\\", "/") else "."
sys.path.insert(0, _here)
sys.path.insert(0, _here + "/..")

import shim  # noqa: E402

shim.install()

from picolm_lib import selftest  # noqa: E402

args = sys.argv[1:]
model = args[0] if args else "models/picolm-s-int8.plm"
mode = args[1] if len(args) > 1 else "auto"
max_cases = int(args[2]) if len(args) > 2 else None
res = selftest.run(model, mode, max_cases=max_cases, bench_tokens=8 if sys.implementation.name != "micropython" else 32)
print(res)
passed, total = res["golden"].split("/")
ok = passed == total != "0" and res.get("alloc_per_token", 0) == 0
sys.exit(0 if ok else 1)
