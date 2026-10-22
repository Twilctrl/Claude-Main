"""Run the device self-test on an emulated ARM MCU (QEMU) from a ROMFS image of the SD build.

Linux / WSL only. Needs a MicroPython checkout with:
  - mpy-cross built                (make -C mpy-cross)
  - the QEMU port built as below, which makes the native code generator emit
    ARMv6-M code like the RP2040's and limits the heap to ~100 KB:
        make -C ports/qemu BOARD=MPS2_AN385 BUILD=build-armv6m \\
             MICROPY_HEAP_SIZE=102400 CFLAGS_EXTRA=-DMICROPY_EMIT_THUMB_ARMV7M=0
  - qemu-system-arm and pyserial installed

From projects/picolm:
    python device/sim/qemu_selftest.py --micropython ~/micropython [--model models/picolm-s-int8.plm]
    python device/sim/qemu_selftest.py --micropython ~/micropython --ui   # the UI scenarios instead

QEMU checks correctness, memory and allocations on real Thumb code; its speed
says nothing about the PicoCalc.
"""

import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPT = """
import gc, sys
gc.collect()
print("heap free before import:", gc.mem_free())
sys.path.append("/rom")
from picolm_lib import selftest
gc.collect()
print("heap free after importing the engine:", gc.mem_free())
r = selftest.run("/rom/picolm_lib/model.plm", "sd", bench_tokens=16)
print("RESULT", r)
"""

UI_SCRIPT = """
import gc, sys
sys.path.append("/rom")
gc.collect()
free0 = gc.mem_free()
import ui_scenario as sc
m = "/rom/picolm_lib/model.plm"
d = sc.Driver(m)
gc.collect()
print("heap free before the app:", free0, "| used by app, engine and simulator modules:", free0 - gc.mem_free())
d.close()
sc.scenario_golden_story(m)
sc.scenario_prompt_scroll_stop(m)
d = sc.Driver(m, record=False)
d.vm.draw.quiet = True  # the real framebuffer is in PSRAM, so do not count simulated glyphs
d.press(sc.BUTTON_DOWN)
d.press(sc.BUTTON_CENTER)
d.finish()
gc.collect()
print("heap free after a %d-token story: %d of %d" % (d.ui.n, gc.mem_free(), free0))
d.close()
print("UI scenarios passed")
"""
SIM_FILES = ["fakevm.py", "shim.py", "ui_scenario.py", "picoware/__init__.py", "picoware/system/__init__.py",
             "picoware/system/buttons.py", "picoware/system/vector.py", "picoware/gui/__init__.py",
             "picoware/gui/menu.py", "picoware/gui/textbox.py"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--micropython", required=True, help="MicroPython checkout")
    ap.add_argument("--model", default=os.path.join(ROOT, "models", "picolm-s-int8.plm"))
    ap.add_argument("--ui", action="store_true", help="run the UI scenarios (fake Picoware, no PSRAM)")
    args = ap.parse_args()
    mp = os.path.abspath(os.path.expanduser(args.micropython))
    firmware = os.path.join(mp, "ports", "qemu", "build-armv6m", "firmware.elf")

    with tempfile.TemporaryDirectory() as tmp:
        sd = os.path.join(tmp, "sd")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_sd.py"), "--model", args.model,
                        "--out", sd, "--mpy-cross", os.path.join(mp, "mpy-cross", "build", "mpy-cross")],
                       check=True, stdout=subprocess.DEVNULL)
        apps = os.path.join(sd, "picoware", "apps")
        if args.ui:  # add the app as main.py plus the simulated Picoware, compiled like the rest
            mpy_cross = os.path.join(mp, "mpy-cross", "build", "mpy-cross")
            here = os.path.dirname(os.path.abspath(__file__))
            for rel in SIM_FILES + ["../main.py"]:
                out = os.path.join(apps, os.path.normpath(rel.replace("../", "")))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                subprocess.run([mpy_cross, "-march=armv6m", "-o", out[:-3] + ".mpy",
                                os.path.join(here, rel)], check=True)
        sys.path.insert(0, os.path.join(mp, "tools", "mpremote"))
        from mpremote.romfs import make_romfs

        img = os.path.join(tmp, "romfs.img")
        with open(img, "wb") as f:
            f.write(make_romfs(apps, mpy_cross=False))
        script = os.path.join(tmp, "selftest.py")
        with open(script, "w") as f:
            f.write(UI_SCRIPT if args.ui else SCRIPT)
        qemu = ("qemu-system-arm -machine mps2-an385 -nographic -monitor null -semihosting -serial pty "
                f"-kernel {firmware} -device loader,file={img},addr=0x21000000,force-raw=on")
        out = subprocess.run([sys.executable, os.path.join(mp, "tools", "pyboard.py"), "--device",
                              "execpty:" + qemu, script], capture_output=True, text=True, timeout=1800)
    print(out.stdout + out.stderr)
    if args.ui:
        ok = "UI scenarios passed" in out.stdout
    else:
        ok = "'golden': '3/3'" in out.stdout and "'alloc_per_token': 0" in out.stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
